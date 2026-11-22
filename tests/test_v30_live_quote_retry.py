"""V30: claim-complete literal municipal quotations without invented support."""

import json

import pytest

from prawdowid.application.evidence import EvidenceService, _focused_live_quotes
from prawdowid.domain.models import Claim, EvidencePassage, Verdict
from prawdowid.infrastructure.model_transport import SYSTEM_MESSAGE

CLAIM = "Władze Krakowa zaproponowały zmniejszenie SCT."
FIRST = (
    "Trwają konsultacje społeczne w sprawie Strefy Czystego Transportu (SCT). "
    "Niezależnie od nich miasto przygotowało projekt zmiany uchwały o SCT. "
    "Projekt dotyczy zasięgu strefy i zasad jej działania."
)
SECOND = (
    "Najważniejsze założenia projektu\n\nMniejsza strefa. "
    "Obecnie SCT obejmuje ok. 196 km². Po zmianie obejmie ok. 0,99 km² w centrum."
)
BODY = FIRST + "\n\n" + SECOND


def passage(text=BODY):
    return EvidencePassage(
        passage_id="live:example:0",
        document_id="live:example",
        exact_text=text,
        publisher="Zarząd Dróg Miasta Krakowa",
        title="Miasto proponuje zmiany",
        url="https://zdmk.krakow.pl/nasze-dzialania/example/",
        published_at=None,
    )


def test_focused_quotes_are_literal_and_cover_both_predicates():
    options = _focused_live_quotes(CLAIM, (passage(),))
    assert options
    assert options[0]["quote"] in BODY
    assert "projekt" in options[0]["quote"].casefold()
    assert "mniejsza" in options[0]["quote"].casefold()
    assert len(options[0]["quote"]) <= 500
    assert not _focused_live_quotes(CLAIM, (passage("Ceny benzyny wzrosły."),))
    unrelated = Claim("c1", "Miasto rozwiązało problem smogu w Krakowie.")
    assert not _focused_live_quotes(unrelated.text, (passage(),))
    assert "not Special City Tax" in SYSTEM_MESSAGE


@pytest.mark.asyncio
async def test_failed_short_quote_can_retry_with_literal_complete_candidate():
    class Model:
        def __init__(self):
            self.calls = []

        async def complete_json(self, *, schema, prompt):
            data = json.loads(prompt)
            self.calls.append(data)
            if len(self.calls) == 1:
                # The fragment describes the proposed amendment, but not
                # the reduction itself, hence the V29 gate rejects it.
                return {"citations": [{
                    "passage_id": "live:example:0",
                    "quote": FIRST,
                    "relation": "supports",
                }], "missing_context": False}
            assert data["verification_retry"]
            option = data["exact_quote_options"][0]
            return {"citations": [{
                "passage_id": option["passage_id"],
                "quote": option["quote"],
                "relation": "supports",
            }], "missing_context": False}

    model = Model()
    outcome = await EvidenceService(model).verify(Claim("c1", CLAIM), (passage(),))
    assert outcome.verdict is Verdict.CONFIRMED
    assert len(model.calls) == 2
    assert outcome.citations[0].quote in BODY


@pytest.mark.asyncio
async def test_retry_is_not_automatic_confirmation_or_enacted_rule():
    class Model:
        def __init__(self):
            self.calls = []

        async def complete_json(self, *, schema, prompt):
            data = json.loads(prompt)
            self.calls.append(data)
            return {"citations": [], "missing_context": True}

    model = Model()
    outcome = await EvidenceService(model).verify(Claim("c1", CLAIM), (passage(),))
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 2

    enacted = "Władze Krakowa już wprowadziły zmniejszoną SCT."
    other_model = Model()
    outcome = await EvidenceService(other_model).verify(
        Claim("c1", enacted), (passage(),)
    )
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert len(other_model.calls) == 1


@pytest.mark.asyncio
async def test_no_retry_for_non_reviewed_passages():
    class Model:
        def __init__(self):
            self.calls = []

        async def complete_json(self, *, schema, prompt):
            self.calls.append(json.loads(prompt))
            return {"citations": [], "missing_context": False}

    non_reviewed = EvidencePassage(
        passage_id="regular:1",
        document_id="regular:1",
        exact_text=BODY,
        publisher="Random outlet",
        title="Comment",
        url="https://example.org/",
        published_at=None,
    )
    model = Model()
    outcome = await EvidenceService(model).verify(Claim("c1", CLAIM), [non_reviewed])
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_pipeline_real_evidence_service_with_article_paragraphs():
    """Unlike V29's stubbed verifier, exercise the real quote validator."""
    from types import SimpleNamespace

    from prawdowid.application.analyses import Submission
    from prawdowid.application.pipeline import AnalysisPipeline
    from prawdowid.domain.models import EventQuery
    from prawdowid.infrastructure.discovery import DiscoveryReport
    from prawdowid.infrastructure.live_official import LiveOfficialEvidence
    from prawdowid.infrastructure.safe_web import WebPage

    html = (
        "<html><title>Strefa Czystego Transportu: miasto proponuje zmiany</title>"
        "<body><article><p>" + FIRST + "</p><p>Najważniejsze założenia projektu. "
        "Mniejsza strefa.</p><p>Obecnie SCT obejmuje około 196 km². "
        "Po zmianie obejmie około 0,99 km² w centrum.</p></article></body></html>"
    )

    class Web:
        async def read_article(self, url):
            return WebPage(url, html.encode(), 200, "")

    class ExtractAndVerify:
        async def extract(self, _text):
            return EventQuery(CLAIM), (Claim("c1", CLAIM, "verifiable", "public"),)

        async def verify(self, claim, passages):
            return await EvidenceService(Model()).verify(claim, passages)

    class Model:
        def __init__(self):
            self.called = 0

        async def complete_json(self, *, schema, prompt):
            self.called += 1
            data = json.loads(prompt)
            if "verification_retry" not in data:
                return {"citations": [], "missing_context": False}
            assert data["exact_quote_options"]
            option = data["exact_quote_options"][0]
            return {"citations": [{**option, "relation": "supports"}], "missing_context": False}

    class Retriever:
        async def search(self, _event, _query):
            return SimpleNamespace(passages=(), warnings=())

    class Discovery:
        async def discover(self, _event):
            return DiscoveryReport()

    class Corpus:
        async def enrich(self, _items):
            return False

    report = await AnalysisPipeline(
        ExtractAndVerify(), Retriever(), Discovery(), Corpus(),
        live_official=LiveOfficialEvidence(Web()),
    ).analyze("test-v30", Submission("text", CLAIM, web_search=True))
    assert report["search"]["live_usable"] == 1
    assert report["claims"][0]["verdict"] == "confirmed"
    assert report["sources"]
    assert report["sources"][0]["exact_quote"] in html.replace("</p><p>", "\n\n") or (
        report["sources"][0]["exact_quote"] in FIRST + "\n\nNajważniejsze założenia projektu. Mniejsza strefa."
    )
