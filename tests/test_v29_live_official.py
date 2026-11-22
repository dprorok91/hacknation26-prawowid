"""V29: no speculative claims, no arbitrary website fetching or fake citations."""

from types import SimpleNamespace

import pytest

from prawdowid.api.schemas import AnalysisReport
from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceCitation, Verification, _has_claim_anchors
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.domain.models import Claim, EventQuery, EvidenceRelation, Verdict
from prawdowid.infrastructure.discovery import DiscoveryReport
from prawdowid.infrastructure.live_official import (
    OFFICIAL_SCT_PROPOSAL,
    LiveOfficialEvidence,
    matching_live_passages,
    official_entrypoints,
)
from prawdowid.infrastructure.safe_web import FetchError, WebPage

STATEMENT = "Władze Krakowa zaproponowały zmniejszenie SCT."
BODY = (
    "<html><head><title>Strefa Czystego Transportu: miasto proponuje zmiany</title></head>"
    "<body><main><article><p>Trwają konsultacje społeczne w sprawie Strefy "
    "Czystego Transportu (SCT). Niezależnie od nich miasto przygotowało projekt "
    "zmiany uchwały o SCT. Projekt dotyczy zasięgu strefy i zasad jej działania.</p>"
    "<p>Mniejsza strefa. Obecnie SCT obejmuje około 196 km2. "
    "Po zmianie obejmie około 0,99 km2 w centrum.</p>"
    "<p>Projekt zostanie poddany konsultacjom mieszkańców, a następnie "
    "głosowaniu Rady Miasta Krakowa. Do tego czasu obowiązują dotychczasowe zasady.</p>"
    "</article></main></body></html>"
)


class Web:
    def __init__(self, *, content=BODY, page_url=OFFICIAL_SCT_PROPOSAL, robots=""):
        self.calls = []
        self.content = content
        self.page_url = page_url
        self.robots = robots

    async def read_article(self, url):
        self.calls.append(url)
        return WebPage(self.page_url, self.content.encode(), 200, self.robots)


class Retriever:
    async def search(self, _event, _query):
        return SimpleNamespace(passages=(), warnings=("embedding_unavailable",))


class Discovery:
    async def discover(self, _event):
        return DiscoveryReport()


class Corpus:
    async def enrich(self, _items):
        return False


class Evidence:
    async def extract(self, _text):
        return EventQuery(STATEMENT), (Claim("c1", STATEMENT, scope="public"),)

    async def verify(self, _claim, passages):
        if not passages:
            return Verification(Verdict.INSUFFICIENT_EVIDENCE, "Brak dowodów")
        passage = passages[0]
        quote = passage.exact_text
        assert _has_claim_anchors(STATEMENT, quote)
        return Verification(
            Verdict.CONFIRMED, "Oficjalna strona opisuje projekt zmniejszenia.",
            (EvidenceCitation(passage, quote, EvidenceRelation.SUPPORTS),),
        )


def test_official_entrypoint_is_only_about_krakow_sct_proposal():
    assert official_entrypoints(STATEMENT)[0].canonical_url == OFFICIAL_SCT_PROPOSAL
    assert not official_entrypoints("Moja krowa daje mleko.")
    assert not official_entrypoints("SCT w Warszawie już obowiązuje.")
    assert not official_entrypoints("SCT w Krakowie weszła w życie.")
    assert not official_entrypoints("W Krakowie proponują zmniejszenie podatku od psów.")


@pytest.mark.asyncio
async def test_live_official_supplies_literal_citation_without_corpus_approval():
    web = Web()
    live = LiveOfficialEvidence(web)
    report = await AnalysisPipeline(
        Evidence(), Retriever(), Discovery(), Corpus(), live_official=live
    ).analyze("analysis-v29", Submission("text", STATEMENT, web_search=True))
    AnalysisReport.model_validate(report)
    assert report["search"]["live_attempted"] == 1
    assert report["search"]["live_usable"] == 1
    assert report["claims"][0]["verdict"] == "confirmed"
    assert report["sources"][0]["publisher"] == "Zarząd Dróg Miasta Krakowa"
    assert report["sources"][0]["source_type"] == "primary"
    assert report["sources"][0]["url"] == OFFICIAL_SCT_PROPOSAL
    assert "projekt" in report["sources"][0]["exact_quote"]
    assert len(web.calls) == 1
    assert report["coverage"]["evidence_by_category"]["official"]
    assert report["coverage"]["cited_claims"] == 1


@pytest.mark.asyncio
async def test_private_text_never_triggers_live_official_lookup():
    web = Web()
    report = await AnalysisPipeline(
        Evidence(), Retriever(), Discovery(), Corpus(),
        live_official=LiveOfficialEvidence(web),
    ).analyze("private", Submission("text", STATEMENT, web_search=False))
    assert not web.calls
    assert report["claims"][0]["verdict"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_denied_text_and_redirect_do_not_create_quotes():
    for options in (
        {"content": BODY.replace("<body>", '<meta name="robots" content="noindex"><body>')},
        {"page_url": "https://other.example/news"},
        {"robots": "noarchive"},
    ):
        result = await LiveOfficialEvidence(Web(**options)).collect(
            official_entrypoints(STATEMENT), (Claim("c1", STATEMENT, scope="public"),)
        )
        assert not result.passages
        assert result.usable == 0


@pytest.mark.asyncio
async def test_no_arbitrary_publisher_fetch_and_bounded_pages():
    from prawdowid.domain.models import DocumentCandidate

    candidates = (DocumentCandidate(
        "https://tvn24.pl/news", "TVN24", "News", None, "rss"
    ), *official_entrypoints(STATEMENT))
    web = Web()
    result = await LiveOfficialEvidence(web).collect(
        candidates, (Claim("c1", STATEMENT, scope="public"),)
    )
    assert web.calls == [OFFICIAL_SCT_PROPOSAL]
    assert result.attempted == 1
    assert len(result.passages) <= 3
    assert all(len(p.exact_text) <= 1500 for p in result.passages)
    assert matching_live_passages("Nieznana osoba dokonała przestępstwa", result.passages) == ()


def test_proposal_quote_cannot_confirm_enactment_without_action():
    quote = (
        "Trwają konsultacje w sprawie SCT; miasto przygotowało projekt zmiany "
        "uchwały. Projekt dotyczy zmniejszenia obszaru strefy."
    )
    assert _has_claim_anchors(STATEMENT, quote)
    assert not _has_claim_anchors(
        "Władze Krakowa już zmniejszyły obowiązującą Strefę Czystego Transportu.",
        quote,
    )


@pytest.mark.asyncio
async def test_safe_fetch_error_is_bounded_and_not_citation():
    class Denied:
        async def read_article(self, _url):
            raise FetchError("robots_denied")

    result = await LiveOfficialEvidence(Denied()).collect(
        official_entrypoints(STATEMENT), (Claim("c1", STATEMENT),)
    )
    assert not result.passages
    assert result.errors == ("robots_denied",)

@pytest.mark.asyncio
async def test_real_verifier_checks_literal_quote_and_proposal_status():
    import json

    from prawdowid.application.evidence import EvidenceService

    result = await LiveOfficialEvidence(Web()).collect(
        official_entrypoints(STATEMENT), (Claim("c1", STATEMENT),)
    )
    relevant = matching_live_passages(STATEMENT, result.passages)
    assert relevant

    class Model:
        async def complete_json(self, *, schema, prompt):
            data = json.loads(prompt)
            assert data["task"] == "verify"
            return {"citations": [{
                "passage_id": data["passages"][0]["passage_id"],
                "quote": data["passages"][0]["text"],
                "relation": "supports",
            }]}

    outcome = await EvidenceService(Model()).verify(Claim("c1", STATEMENT), relevant)
    assert outcome.verdict is Verdict.CONFIRMED
    assert outcome.citations[0].quote in relevant[0].exact_text

    # An aggressive model cannot promote a proposal to an enacted rule using
    # exactly the same excerpt. This must remain inconclusive.
    enacted = "Władze Krakowa już wprowadziły zmniejszoną Strefę Czystego Transportu."
    other = await EvidenceService(Model()).verify(Claim("c2", enacted), relevant)
    assert other.verdict is Verdict.INSUFFICIENT_EVIDENCE
