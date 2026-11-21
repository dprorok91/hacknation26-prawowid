"""Analysis orchestration over independently testable retrieval and model adapters."""

from datetime import timedelta
from hashlib import sha256

from prawdowid.application.analyses import AnalysisError, ErrorCode, Submission
from prawdowid.application.narratives import compare_narratives
from prawdowid.domain.models import EventQuery


class AnalysisPipeline:
    def __init__(self, evidence, retrieval, discovery, corpus):
        self.evidence = evidence
        self.retrieval = retrieval
        self.discovery = discovery
        self.corpus = corpus

    async def analyze(self, analysis_id: str, submission: Submission) -> dict:
        text = submission.content
        article = None
        if submission.kind == "url":
            article = await self.corpus.article(text)
            text = f"{article.title}\n\n{article.text}"[:20_000]
        event, claims = await self.evidence.extract(text)
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
        publishers = {
            passage.publisher.casefold() for match in matches.values() for passage in match.passages
        }
        if submission.kind == "text":
            limitations.add("Tekst sprawdzono w lokalnym zbiorze źródeł.")
        if submission.kind == "url" and matches and len(publishers) < 2 and event.query_text:
            discovery = await self.discovery.discover(event)
            if discovery.errors:
                limitations.add("Część źródeł zewnętrznych była niedostępna.")
            if not publishers and not discovery.candidates and discovery.errors:
                raise AnalysisError(
                    ErrorCode.SOURCE_UNAVAILABLE, "Discovery providers were unavailable."
                )
            if discovery.candidates and await self.corpus.enrich(discovery.candidates):
                matches = await self._retrieve(event, claims) or matches

        report = {
            "analysis_id": analysis_id,
            "claims": [],
            "narratives": [],
            "articles": [],
            "sources": [],
            "limitations": [],
        }
        sources = {}
        candidate_ids = set()
        for claim in claims:
            found = matches.get(claim.claim_id)
            passages = found.passages if found else ()
            candidate_ids.update(passage.passage_id for passage in passages)
            if found and found.warnings:
                limitations.add("Dla części zapytań wyszukiwanie wektorowe było niedostępne.")
            decision = await self.evidence.verify(claim, passages)
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
            report["claims"].append(
                {
                    "claim_id": claim.claim_id,
                    "text": claim.text,
                    "verdict": decision.verdict.value,
                    "rationale": decision.rationale,
                    "evidence_source_ids": list(dict.fromkeys(citation_ids)),
                }
            )

        frames = compare_narratives(
            event, [passage for result in matches.values() for passage in result.passages]
        )
        for frame in frames:
            passage = frame.passage
            source_id = _reference(sources, passage, frame.quote, "context")
            report["narratives"].append(
                {
                    "source_id": source_id,
                    "event_id": frame.event_id,
                    "emphasis": [frame.quote],
                    "highlighted_passage_ids": [passage.passage_id],
                    "caveat": "Porównanie opiera się na dostępnych fragmentach i datach publikacji.",
                }
            )
            report["articles"].append(
                {
                    "source_id": source_id,
                    "headline": passage.title,
                    "summary": frame.quote,
                }
            )
        if matches and not frames:
            limitations.add("Za mało niezależnych źródeł o tym samym zdarzeniu do porównania.")
        report["sources"] = list(sources.values())
        report["limitations"] = sorted(limitations)
        report["source_snapshot"] = sha256("\n".join(sorted(candidate_ids)).encode()).hexdigest()
        return report

    async def _retrieve(self, event, claims):
        return {
            claim.claim_id: await self.retrieval.search(event, claim.text)
            for claim in claims
            if claim.kind == "verifiable"
        }


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
            "title": passage.title,
            "url": passage.url,
            "published_at": _timestamp(passage.published_at),
            "fetched_at": _timestamp(getattr(passage, "fetched_at", None)),
            "exact_quote": quote,
            "supports_claim_ids": [],
            "relation": relation,
        }
    return source_id
