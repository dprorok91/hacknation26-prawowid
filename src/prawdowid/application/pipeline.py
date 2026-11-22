"""Analysis orchestration over independently testable retrieval and model adapters."""

from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from prawdowid.application.analyses import Submission
from prawdowid.application.claims import private_reference
from prawdowid.application.narratives import compare_narratives
from prawdowid.domain.models import EventQuery
from prawdowid.domain.semantics import aliases_for, expand_query


class AnalysisPipeline:
    def __init__(self, evidence, retrieval, discovery, corpus, factchecks=None):
        self.evidence = evidence
        self.retrieval = retrieval
        self.discovery = discovery
        self.corpus = corpus
        self.factchecks = factchecks

    async def analyze(self, analysis_id: str, submission: Submission, *, article=None, evidence=None) -> dict:
        text = submission.content
        evidence = self.evidence if evidence is None else evidence
        if submission.kind == "url":
            if article is None:
                article = await self.corpus.article(text)
            text = f"{article.title}\n\n{article.text}"[:20_000]
        event, claims = await evidence.extract(text)
        if article and article.published_at and not event.occurred_after:
            event = EventQuery(
                query_text=event.query_text,
                entities=event.entities,
                location=event.location,
                occurred_after=article.published_at - timedelta(days=14),
                occurred_before=article.published_at + timedelta(days=7),
            )

        matches = await self._retrieve(event, claims)
        limitations = set()
        discovered_links: list[dict] = []
        fact_checks: list[dict] = []
        fact_check_errors: list[str] = []
        search = {
            "requested": submission.kind == "url" or submission.web_search,
            "attempted": False,
            "status": "disabled",
            "candidates_found": 0,
            "provider_errors": [],
            "per_claim": [],
            "queries": [],
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
                    if item.canonical_url in by_url:
                        if claim.claim_id not in by_url[item.canonical_url]["claim_ids"]:
                            by_url[item.canonical_url]["claim_ids"].append(claim.claim_id)
                    else:
                        by_url[item.canonical_url] = {
                            "title": item.headline,
                            "publisher": item.publisher,
                            "url": item.canonical_url,
                            "provider": item.provider,
                            "published_at": _timestamp(item.published_at),
                            "category": _publisher_category(item.publisher, item.canonical_url),
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
                # Metadata are not evidence. Only approved article content may
                # be fetched and supplied to the verifier.
                updated = await self.corpus.enrich(tuple(ingest_candidates.values()))
                if updated:
                    matches = await self._retrieve(event, claims)
                else:
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
            "annotated_text": text[:5000] if submission.kind == "text" else None,
            "coverage": {},
            "fact_checks": fact_checks,
            "fact_check_errors": sorted(set(fact_check_errors)),
        }
        sources = {}
        candidate_ids = set()
        offset_cursor = 0
        for claim in claims:
            found = matches.get(claim.claim_id)
            passages = found.passages if found else ()
            candidate_ids.update(passage.passage_id for passage in passages)
            if found and found.warnings:
                limitations.add("Dla części zapytań wyszukiwanie wektorowe było niedostępne.")
            decision = await evidence.verify(claim, passages)
            citation_ids = []
            for citation in decision.citations:
                passage = citation.passage
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
        cited_publishers = sorted({row["publisher"] for row in sources.values()})
        discovered_publishers = sorted({
            item["publisher"] for item in discovered_links if item["publisher"]
        })
        report["coverage"] = {
            "evidence_publishers": cited_publishers,
            "fact_check_evidence_publishers": sorted({
                row["publisher"] for row in sources.values()
                if row.get("source_type") == "factcheck"
            }),
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

    async def _retrieve(self, event, claims):
        return {
            claim.claim_id: await self.retrieval.search(
                _event_for_claim(event, claim, total_claims=len(claims)),
                expand_query(claim.text),
            )
            for claim in claims
            if claim.kind == "verifiable"
            and claim.scope == "public"
            and not private_reference(claim.text)
        }


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
        references[source_id] = {
            "source_id": source_id,
            "passage_id": passage.passage_id,
            "publisher": passage.publisher,
            "source_type": getattr(passage, "source_type", "news"),
            "title": passage.title,
            "url": passage.url,
            "published_at": _timestamp(passage.published_at),
            "fetched_at": _timestamp(getattr(passage, "fetched_at", None)),
            "exact_quote": quote,
            "supports_claim_ids": [],
            "relation": relation,
        }
    return source_id


def _publisher_category(publisher: str, url: str) -> str:
    """Recognize specialized fact-checking publishers, never accept their verdict blindly."""
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    name = publisher.casefold()
    known_hosts = ("demagog.org.pl", "konkret24.tvn24.pl", "sprawdzam.afp.com")
    if any(host == item or host.endswith("." + item) for item in known_hosts):
        return "fact_check"
    # Aggregators commonly hide the original domain, but preserve the publisher.
    if any(term in name for term in ("demagog", "konkret24", "afp sprawdzam")):
        return "fact_check"
    return "news"
