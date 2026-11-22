"""End-to-end in-process regression: an article, four claims and four real labels.

The passages below are synthetic fixtures; they never claim that the live
PostgreSQL already has licensed media/fact-check passages.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceCitation, Verification
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.domain.models import Claim, EventQuery, EvidencePassage, EvidenceRelation, Verdict
from prawdowid.infrastructure.retrieval import SearchResult

PUBLISHERS = (
    ("https://www.gov.pl/web/rcb/komunikat", "RCB", "official", "core"),
    ("https://www.pap.pl/aktualnosci/komunikat", "PAP", "major_media", "core"),
    ("https://demagog.org.pl/analizy/komunikat", "Demagog", "fact_check", "core"),
    ("https://regionalna-prasa.example/news", "Regionalne wiadomości", "other_media", "perspective"),
)


class FourClaimEvidence:
    def __init__(self, passages):
        self.passages = passages

    async def extract(self, text):
        claims = tuple(
            Claim(f"c{index}", phrase, "verifiable", "public", "Sprawdzić urzędowy dokument.")
            for index, phrase in enumerate(text.split("\n"), 1)
        )
        return EventQuery(query_text="komunikat"), claims

    async def verify(self, claim, evidence):
        index = int(claim.claim_id[1:]) - 1
        passage = self.passages[index]
        assert passage in evidence
        return Verification(
            Verdict.CONFIRMED,
            "Cytat w danych testowych potwierdza tezę.",
            (EvidenceCitation(passage, passage.exact_text, EvidenceRelation.SUPPORTS),),
        )


class FourClaimRetriever:
    def __init__(self, passages):
        self.passages = passages

    async def search(self, event, query):
        return SearchResult(tuple(self.passages))


@pytest.mark.asyncio
async def test_four_claim_article_cites_all_available_categories_with_roles():
    passages = tuple(
        EvidencePassage(
            f"p{i}", f"d{i}", f"Treść cytatu numer {i} z wydarzenia testowego.",
            publisher, f"Dokument {i}", url, datetime(2026, 11, 22, tzinfo=UTC),
        )
        for i, (url, publisher, _, _) in enumerate(PUBLISHERS)
    )
    article = "\n".join(f"Testowe twierdzenie {index}." for index in range(1, 5))
    pipeline = AnalysisPipeline(
        FourClaimEvidence(passages), FourClaimRetriever(passages),
        SimpleNamespace(), SimpleNamespace(),
    )
    result = await pipeline.analyze("test-id", Submission("text", article))
    assert len(result["claims"]) == 4
    assert all(claim["verdict"] == "confirmed" for claim in result["claims"])
    cited = {source["source_category"]: source for source in result["sources"]}
    assert set(cited) == {category for _, _, category, _ in PUBLISHERS}
    for url, _, category, role in PUBLISHERS:
        assert (cited[category]["url"], cited[category]["source_role"]) == (url, role)
    assert result["coverage"]["evidence_categories_missing"] == []
    for claim in result["claims"]:
        assert len(claim["evidence_source_ids"]) == 1


@pytest.mark.asyncio
async def test_unrepresented_categories_are_reported_missing_not_fabricated():
    passage = EvidencePassage(
        "p1", "d1", "SCT obowiązuje od 01.01.2026 r.", "BIP Krakowa", "SCT",
        "https://www.bip.krakow.pl/?dok_id=226666", datetime(2026, 11, 22, tzinfo=UTC),
    )

    class SingleEvidence(FourClaimEvidence):
        async def extract(self, text):
            return EventQuery(query_text=text), (Claim("c1", text, "verifiable", "public"),)

    pipeline = AnalysisPipeline(
        SingleEvidence((passage,)), FourClaimRetriever((passage,)),
        SimpleNamespace(), SimpleNamespace(),
    )
    report = await pipeline.analyze(
        "test-single", Submission("text", "SCT obowiązuje od 01.01.2026 r.")
    )
    assert set(report["coverage"]["evidence_categories_missing"]) == {
        "major_media", "fact_check", "other_media",
    }
    assert report["coverage"]["evidence_by_category"]["official"] == ["BIP Krakowa"]
    assert len(report["sources"]) == 1


def test_article_script_summarizes_only_cited_categories():
    from pathlib import Path
    from runpy import run_path

    script = Path(__file__).resolve().parents[1] / "scripts/test_article_source_flags.py"
    summarize = run_path(str(script))["summarize"]
    report = {
        "claims": [{"text": "Claim", "verdict": "false", "evidence_source_ids": ["s1"]}],
        "sources": [{
            "source_id": "s1", "source_category": "official", "source_role": "core",
            "exact_quote": "Example", "url": "https://www.gov.pl/doc", "publisher": "Gov.pl",
        }],
    }
    summary = summarize(report)
    assert summary["categories_without_cited_evidence"] == [
        "major_media", "fact_check", "other_media",
    ]
    assert summary["claims"][0]["citations"][0]["quote"] == "Example"
