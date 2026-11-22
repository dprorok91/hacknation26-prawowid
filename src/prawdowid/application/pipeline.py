"""Analysis orchestration over independently testable retrieval and model adapters."""

import re
from dataclasses import dataclass, replace
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from prawdowid.application.analyses import Submission
from prawdowid.application.claim_batches import claim_batches
from prawdowid.application.claims import candidate_spans, private_reference
from prawdowid.application.narratives import compare_narratives
from prawdowid.domain.models import EventQuery
from prawdowid.domain.semantics import aliases_for, expand_query
from prawdowid.domain.source_flags import classify_source
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.live_official import (
    matching_live_passages,
    official_entrypoints,
)


class AnalysisPipeline:
    def __init__(self, evidence, retrieval, discovery, corpus, factchecks=None, live_official=None):
        self.evidence = evidence
        self.retrieval = retrieval
        self.discovery = discovery
        self.corpus = corpus
        self.factchecks = factchecks
        self.live_official = live_official

    async def analyze(self, analysis_id: str, submission: Submission, *, article=None, evidence=None) -> dict:
        text = submission.content
        evidence = self.evidence if evidence is None else evidence
        truncated_article = False
        if submission.kind == "url":
            if article is None:
                article = await self.corpus.article(text)
            source_text = f"{article.title or ''}\n\n{article.text}"
            truncated_article = len(source_text) > 20_000
            text = source_text[:20_000]
        article_segments = len(candidate_spans(text))
        batches = claim_batches(text)
        extracted = [await evidence.extract(batch) for batch in batches]
        event = extracted[0][0]
        unique = {}
        for _, propositions in extracted:
            for proposition in propositions:
                unique.setdefault(proposition.text.casefold(), proposition)
        claims = tuple(
            replace(proposition, claim_id=f"c{index}")
            for index, proposition in enumerate(unique.values(), start=1)
        )
        if article and article.published_at and not event.occurred_after:
            event = EventQuery(
                query_text=event.query_text,
                entities=event.entities,
                location=event.location,
                occurred_after=article.published_at - timedelta(days=14),
                occurred_before=article.published_at + timedelta(days=7),
            )

        referents = _article_referents(text, claims)
        matches = await self._retrieve(event, claims, referents=referents)
        limitations = set()
        if len(batches) > 1:
            limitations.add(
                "Długi materiał przeanalizowano w dwóch partiach; "
                "wybrano maksymalnie 10 twierdzeń, nie wszystkie zdania artykułu."
            )
        if article_segments > len(claims):
            limitations.add(
                "Nie wszystkie segmenty tekstu zostały ocenione. "
                "To analiza częściowa, nie weryfikacja całego artykułu."
            )
        if truncated_article:
            limitations.add("Materiał przekroczył 20 000 znaków; końcową część pominięto.")
        discovered_links: list[dict] = []
        fact_checks: list[dict] = []
        live_passages = ()
        fact_check_errors: list[str] = []
        search = {
            "requested": submission.kind == "url" or submission.web_search,
            "attempted": False,
            "status": "disabled",
            "candidates_found": 0,
            "provider_errors": [],
            "per_claim": [],
            "queries": [],
            "live_attempted": 0,
            "live_usable": 0,
        }
        if submission.kind == "text" and not submission.web_search:
            limitations.add("Tekst sprawdzono tylko w lokalnym zbiorze źródeł.")
        public_lookup = submission.kind == "url" or (
            submission.kind == "text" and submission.web_search
        )
        public_claims = [
            claim for claim in claims
            if claim.kind == "verifiable"
            and claim.scope == "public"
            and not private_reference(claim.text)
        ]
        if public_lookup and not public_claims:
            search["status"] = "skipped_no_public_claims"
            limitations.add("Brak twierdzeń nadających się do wyszukiwania w źródłach publicznych.")
        elif public_lookup:
            # Claims are the unit of analysis. Search each distinct public
            # proposition independently, bounded by the provider budget.
            by_url: dict[str, dict] = {}
            ingest_candidates = {}
            for claim in public_claims:
                current = matches.get(claim.claim_id)
                local_publishers = {
                    item.publisher.casefold() for item in (current.passages if current else ())
                }
                if len(local_publishers) >= 2:
                    search["per_claim"].append({
                        "claim_id": claim.claim_id, "status": "sufficient_local_sources"
                    })
                    continue
                if sum(row["status"] == "attempted" for row in search["per_claim"]) >= 3:
                    search["per_claim"].append({
                        "claim_id": claim.claim_id, "status": "budget_exhausted"
                    })
                    limitations.add(
                        "Limit trzech kwerend online na analizę; "
                        "część tez pozostaje bez nowego wyszukania."
                    )
                    continue
                search["attempted"] = True
                search["per_claim"].append({"claim_id": claim.claim_id, "status": "attempted"})
                claim_event = _event_for_claim(event, claim, total_claims=len(claims))
                discovery = await self.discovery.discover(claim_event)
                search["queries"].extend(
                    {"claim_id": claim.claim_id, **item}
                    for item in getattr(discovery, "queries", ())
                )
                search["provider_errors"].extend(
                    {"provider": err.provider, "code": err.error_code, "claim_id": claim.claim_id}
                    for err in discovery.errors
                )
                for item in discovery.candidates:
                    ingest_candidates.setdefault(item.canonical_url, item)
                    if (
                        item.canonical_url in by_url
                        and claim.claim_id not in by_url[item.canonical_url]["claim_ids"]
                    ):
                        by_url[item.canonical_url]["claim_ids"].append(claim.claim_id)
                    elif item.canonical_url not in by_url:
                        flags = classify_source(
                            item.canonical_url, publisher=item.publisher,
                            provider=item.provider, metadata_only=True,
                        )
                        by_url[item.canonical_url] = {
                            "title": item.headline,
                            "publisher": item.publisher,
                            "url": item.canonical_url,
                            "provider": item.provider,
                            "published_at": _timestamp(item.published_at),
                            "category": (
                                "fact_check" if flags.source_category == "fact_check" else "news"
                            ),
                            "source_category": flags.source_category,
                            "source_role": flags.source_role,
                            "claim_ids": [claim.claim_id],
                        }
            # A reviewed municipal source is a starting URL, never a model-made
            # citation. Its body must pass the same live fetch and quote checks.
            for claim in (public_claims if self.live_official is not None else ()):
                for item in official_entrypoints(claim.text):
                    ingest_candidates.setdefault(item.canonical_url, item)
                    if item.canonical_url not in by_url:
                        flags = classify_source(item.canonical_url, publisher=item.publisher)
                        by_url[item.canonical_url] = {
                            "title": item.headline,
                            "publisher": item.publisher,
                            "url": item.canonical_url,
                            "provider": item.provider,
                            "published_at": None,
                            "category": "news",
                            "source_category": flags.source_category,
                            "source_role": flags.source_role,
                            "claim_ids": [claim.claim_id],
                        }
            # Preserve the bounded provider results, including paid-search official
            # links that arrive after Google News results. Metadata are not evidence.
            discovered_links = list(by_url.values())[:200]
            search["candidates_found"] = len(by_url)
            search["status"] = (
                "providers_partial" if search["provider_errors"] else
                "completed" if search["attempted"] else "sufficient_local_sources"
            )
            if search["provider_errors"]:
                limitations.add("Część usług wyszukiwania wiadomości była niedostępna.")
            if self.factchecks is not None:
                for claim in public_claims[:3]:
                    reviews, errors = await self.factchecks.search(claim.text)
                    fact_checks.extend(review.as_report(claim.claim_id) for review in reviews)
                    fact_check_errors.extend(errors)
            if ingest_candidates:
                # The corpus stores only domain-approved text. Separately, a
                # narrowly reviewed municipal portal may provide ephemeral,
                # short excerpts, with no persistent article copy.
                if self.live_official is not None:
                    live = await self.live_official.collect(
                        tuple(ingest_candidates.values()), tuple(public_claims)
                    )
                    live_passages = live.passages
                    search["live_attempted"] = live.attempted
                    search["live_usable"] = live.usable
                    if live.errors:
                        limitations.add(
                            "Części oficjalnych stron nie udało się wykorzystać "
                            "jako źródła tekstowego."
                        )
                updated = await self.corpus.enrich(tuple(ingest_candidates.values()))
                if updated:
                    matches = await self._retrieve(event, claims, referents=referents)
                elif not live_passages:
                    limitations.add(
                        "Znaleziono odnośniki w sieci, ale brak dostępnej treści "
                        "z zatwierdzonych źródeł. Metadane nie są dowodem."
                    )
            elif search["attempted"] and search["provider_errors"]:
                limitations.add("Nie udało się wyszukać nowych źródeł.")

        report = {
            "analysis_id": analysis_id,
            "claims": [],
            "narratives": [],
            "articles": [],
            "sources": [],
            "limitations": [],
            "discovered_links": discovered_links,
            "search": search,
            # A private text analysis is accessible only via its unguessable ID
            # and expires under the existing text retention policy.
            "annotated_text": text[:20_000] if submission.kind == "text" else None,
            "coverage": {},
            "fact_checks": fact_checks,
            "fact_check_errors": sorted(set(fact_check_errors)),
        }
        sources = {}
        candidate_ids = set()
        offset_cursor = 0
        for claim in claims:
            found = matches.get(claim.claim_id)
            local_passages = found.passages if found else ()
            live_for_claim = matching_live_passages(claim.text, live_passages)
            # Reserve room for on-demand primary evidence even if the local
            # top-ten contains old, topic-only hits. LLM still validates quotes.
            passages = tuple({item.passage_id: item for item in (*live_for_claim, *local_passages)}.values())[:10]
            candidate_ids.update(passage.passage_id for passage in passages)
            if found and found.warnings:
                limitations.add("Dla części zapytań wyszukiwanie wektorowe było niedostępne.")
            # Resolve "tego dnia" / "datą obchodów" only from the immediately
            # preceding named event within the SAME paragraph. This identifies
            # the referent; it is not supporting evidence or a source quote.
            referent = referents.get(claim.claim_id)
            verification_claim = (
                replace(claim, text=f"{referent}. {claim.text}") if referent else claim
            )
            decision = await evidence.verify(verification_claim, passages)
            citation_ids = []
            seen_citations = set()
            for citation in decision.citations:
                passage = citation.passage
                # Duplicate copies of an identical quotation on the same host
                # are not multiple independent pieces of evidence.
                fingerprint = (
                    urlsplit(passage.url).hostname,
                    " ".join(citation.quote.split()).casefold(),
                    citation.relation,
                )
                if fingerprint in seen_citations:
                    continue
                seen_citations.add(fingerprint)
                source_id = _reference(sources, passage, citation.quote, citation.relation)
                if (
                    citation.relation == "supports"
                    and claim.claim_id not in sources[source_id]["supports_claim_ids"]
                ):
                    sources[source_id]["supports_claim_ids"].append(claim.claim_id)
                citation_ids.append(source_id)
            start = text.find(claim.text, offset_cursor)
            if start < 0:
                start = text.find(claim.text)
            end = start + len(claim.text) if start >= 0 else None
            if end is not None:
                offset_cursor = end
            private = claim.scope == "private" or private_reference(claim.text)
            report["claims"].append(
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "span_start": start if start >= 0 else None,
                    "span_end": end,
                    "claim_type": "factual" if claim.kind == "verifiable" else (
                        "opinion" if claim.kind == "opinion" else
                        "uncertain" if claim.kind == "unsupported_scope" else "fragment"
                    ),
                    "evidence_scope": "private" if private else (
                        "public" if claim.scope == "public" else "unknown"
                    ),
                    "empirical_test": claim.empirical_test or None,
                    "classification_reason": claim.classification_reason or None,
                    "classification_reviewed": claim.reviewed,
                    "assumptions": [
                        (
                            "Twierdzenie odwołuje się do konkretnego prywatnego przypadku. "
                            "Ogólne informacje nie potwierdzają tego indywidualnego faktu."
                        )
                    ] if private else [],
                    "aliases": list(aliases_for(claim.text)),
                    "topic_links_count": sum(
                        claim.claim_id in item["claim_ids"] for item in discovered_links
                    ),
                    "verdict": decision.verdict.value,
                    "rationale": decision.rationale,
                    "evidence_source_ids": list(dict.fromkeys(citation_ids)),
                }
            )

        framed = set()
        for claim in claims:
            found = matches.get(claim.claim_id)
            if not found:
                continue
            claim_event = _event_for_claim(event, claim, total_claims=len(claims))
            frames = compare_narratives(claim_event, found.passages)
            for frame in frames:
                passage = frame.passage
                source_id = _reference(sources, passage, frame.quote, "context")
                key = (claim.claim_id, source_id, frame.event_id)
                if key in framed:
                    continue
                framed.add(key)
                report["narratives"].append(
                    {
                        "claim_id": claim.claim_id,
                        "source_id": source_id,
                        "event_id": frame.event_id,
                        "emphasis": [frame.quote],
                        "highlighted_passage_ids": [passage.passage_id],
                        "caveat": "Porównanie opiera się na dostępnych fragmentach i datach publikacji.",
                    }
                )
                report["articles"].append(
                    {"source_id": source_id, "headline": passage.title, "summary": frame.quote}
                )
        if matches and not framed:
            limitations.add("Za mało niezależnych źródeł o tym samym zdarzeniu do porównania.")
        report["sources"] = list(sources.values())
        # Discovery coverage is topic-level, never mislabeled as claim support.
        claim_cited_ids = {
            source_id for claim_row in report["claims"]
            for source_id in claim_row["evidence_source_ids"]
        }
        cited_sources = [
            row for source_id, row in sources.items() if source_id in claim_cited_ids
        ]
        cited_publishers = sorted({row["publisher"] for row in cited_sources})
        discovered_publishers = sorted({
            item["publisher"] for item in discovered_links if item["publisher"]
        })
        evidence_by_category = {
            category: sorted({
                row["publisher"] for row in cited_sources
                if row.get("source_category") == category
            })
            for category in ("official", "major_media", "fact_check", "other_media")
        }
        cited_claims = sum(bool(row["evidence_source_ids"]) for row in report["claims"])
        incomplete = (
            article_segments > len(claims)
            or truncated_article
            or (submission.kind == "url" and len(claims) < 2)
            or (submission.kind == "url" and len(text) >= 600 and len(claims) < 3)
        )
        if not cited_claims:
            limitations.add("Nie zweryfikowano żadnej tezy cytatem z niezależnego źródła.")
        report["coverage"] = {
            "input_chars": len(text),
            "input_segments": article_segments,
            "extracted_claims": len(claims),
            "cited_claims": cited_claims,
            "partial_analysis": incomplete or cited_claims == 0,
            "evidence_by_category": evidence_by_category,
            "evidence_categories_missing": [
                category for category, publishers in evidence_by_category.items()
                if not publishers
            ],
            "evidence_publishers": cited_publishers,
            "fact_check_evidence_publishers": evidence_by_category["fact_check"],
            "topic_publishers": discovered_publishers,
            "fact_check_candidates": [
                item for item in discovered_links if item["category"] == "fact_check"
            ],
            "note": (
                "Publikacje znalezione na temat nie muszą odnosić się do tego samego "
                "zdarzenia ani potwierdzać analizowanego twierdzenia."
            ),
        }
        report["limitations"] = sorted(limitations)
        report["source_snapshot"] = sha256("\n".join(sorted(candidate_ids)).encode()).hexdigest()
        return report

    async def _retrieve(self, event, claims, *, referents=None):
        matches = {}
        for claim in claims:
            if claim.kind != "verifiable" or claim.scope != "public" or private_reference(claim.text):
                continue
            query_event = _event_for_claim(event, claim, total_claims=len(claims))
            primary = await self.retrieval.search(query_event, expand_query(claim.text))
            # Anaphora without a subject is often ranked behind unrelated hits.
            # A second, local-only query uses the preceding named referent.
            # Prefer those hits, but keep original results as fallback.
            referent = (referents or {}).get(claim.claim_id)
            if referent:
                contextual = await self.retrieval.search(
                    query_event,
                    f"{referent} {expand_query(claim.text)}",
                )
                primary = _merge_retrieval(contextual, primary)
            # Local-only second query to counterbalance an absolute/exceptionless claim.
            # The verifier still requires exact source quotations for any verdict.
            if re.search(
                r"\b(?:każd[yae]|wszys(?:cy|tkie|tkich|tko)|bez wyjątków?)\b",
                claim.text,
                re.IGNORECASE,
            ):
                counter = await self.retrieval.search(
                    query_event, f"{expand_query(claim.text)} wyjątki dopuszczenie warunki",
                )
                primary = _merge_retrieval(primary, counter)
            matches[claim.claim_id] = primary
        return matches


@dataclass(frozen=True)
class _MergedRetrieval:
    """Minimal result contract accepted by pipeline and retrieval test doubles."""

    passages: tuple
    warnings: tuple[str, ...]


def _merge_retrieval(primary, secondary):
    """Keep distinct passages from both local queries without losing warnings."""
    # Reserve room for counterexamples even when the primary query returns
    # a full top-ten set; otherwise the second search never affects evidence.
    main = list(primary.passages)
    main_ids = {item.passage_id for item in main}
    contrasting = [
        item for item in secondary.passages if item.passage_id not in main_ids
    ]
    balanced = [*main[:6], *contrasting[:4], *main[6:], *contrasting[4:]]
    items = {}
    for item in balanced:
        items.setdefault(item.passage_id, item)
    return _MergedRetrieval(
        passages=tuple(items.values())[:10],
        warnings=tuple(dict.fromkeys((*primary.warnings, *secondary.warnings))),
    )


def _event_for_claim(event: EventQuery, claim, *, total_claims: int) -> EventQuery:
    if total_claims == 1:
        # Use the full proposition, not a possibly shortened model-made event query.
        return EventQuery(
            query_text=claim.text,
            entities=event.entities,
            location=event.location,
            occurred_after=event.occurred_after,
            occurred_before=event.occurred_before,
        )
    # Prevent entities/dates from one assertion from leaking into a different
    # one in an article containing unrelated statements.
    lowered = claim.text.casefold()
    return EventQuery(
        query_text=claim.text,
        entities=tuple(name for name in event.entities if name.casefold() in lowered),
        location=event.location if event.location and event.location.casefold() in lowered else None,
        occurred_after=(
            event.occurred_after if event.occurred_after
            and event.occurred_after.date().isoformat() in claim.text else None
        ),
        occurred_before=(
            event.occurred_before if event.occurred_before
            and event.occurred_before.date().isoformat() in claim.text else None
        ),
    )


def _timestamp(value):
    return value.isoformat() if value else None


def _reference(references, passage, quote: str, relation: str) -> str:
    quote_key = sha256(f"{relation}:{quote}".encode()).hexdigest()[:12]
    source_id = f"{passage.passage_id}:{quote_key}"
    if source_id not in references:
        # One source-domain row serves all ministries on the government portal.
        # Its display_name must not be attributed to every /web/<ministry> URL.
        publisher = (
            "Portal Gov.pl" if urlsplit(passage.url).hostname == "www.gov.pl"
            else passage.publisher
        )
        flags = classify_source(passage.url, publisher=publisher)
        references[source_id] = {
            "source_id": source_id,
            "passage_id": passage.passage_id,
            "publisher": publisher,
            "source_type": getattr(passage, "source_type", "news"),
            "source_category": flags.source_category,
            "source_role": flags.source_role,
            "title": passage.title,
            "url": canonical_url(passage.url),
            "published_at": _timestamp(passage.published_at),
            "fetched_at": _timestamp(getattr(passage, "fetched_at", None)),
            "exact_quote": quote,
            "supports_claim_ids": [],
            "relation": relation,
        }
    return source_id


_EVENT_NAME = re.compile(
    r"\b[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+"
    r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+){2,7}\b"
)
_ANAPHORA = re.compile(
    r"\b(?:tego dnia|datą obchodów|data obchodów|obchody tego dnia)\b",
    re.IGNORECASE,
)


def _article_referents(text, claims):
    """Resolve only an immediately preceding named event in one paragraph.

    Do not reinterpret claims or use unrelated paragraphs as evidence.
    Names affect retrieval/LLM disambiguation but never source citations.
    """
    references = {}
    previous = None
    for claim in claims:
        if previous is not None and _ANAPHORA.search(claim.text):
            first = text.find(previous.text)
            second = text.find(claim.text, max(0, first))
            if first >= 0 and second >= first + len(previous.text):
                bridge = text[first + len(previous.text):second]
                if len(bridge) < 40 and "\n\n" not in bridge:
                    names = _EVENT_NAME.findall(previous.text)
                    if names:
                        references[claim.claim_id] = names[-1]
        previous = claim
    return references
