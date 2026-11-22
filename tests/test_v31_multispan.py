"""V31: complementary passage retrieval, quote truth-boundaries and regression cases."""

import json
from types import SimpleNamespace

import pytest

from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.evidence_selection import select
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.domain.models import Claim, EventQuery, Verdict
from prawdowid.infrastructure.discovery import DiscoveryReport
from prawdowid.infrastructure.live_official import (
    LiveOfficialEvidence,
    matching_live_passages,
    official_entrypoints,
)
from prawdowid.infrastructure.safe_web import WebPage

CLAIM = "Władze Krakowa zaproponowały zmniejszenie SCT."
P1 = "Niezależnie od konsultacji miasto przygotowało projekt zmiany uchwały o SCT. Projekt dotyczy zasięgu strefy i zasad jej działania."
P2 = "Najważniejsze założenia projektu"
P3 = "Mniejsza strefa"
PADDING = "Urzędnicy przedstawili także szczegółowy opis konsultacji opłat i sposobu składania uwag. " * 5
P4 = "Obecnie SCT obejmuje ok. 196 km², czyli niemal całe miasto. Po zmianie obejmie ok. 0,99 km² w centrum. Uzasadnienie zmiany opisujemy poniżej."
P5 = "Zakończenie analizy. Harmonogram prac będzie aktualizowany po konsultacjach społecznych, a do tego czasu obowiązują dotychczasowe zasady."
P6 = "Zobacz także: Założenia do projektu uchwały Rady Miasta Krakowa [pdf]"
BODY = f"{P1}\n\n{P2}\n\n{P3}\n\n{PADDING}\n\n{P4}\n\n{P5}\n\n{P6}"


class Web:
    async def read_article(self, url):
        html = "<html><title>Komunikat</title><article>" + "".join(
            f"<p>{p}</p>" for p in (P1, P2, P3, PADDING, P4, P5, P6)
        ) + "</article></html>"
        return WebPage(url, html.encode(), 200, "")


async def get_passages():
    result = await LiveOfficialEvidence(Web()).collect(
        official_entrypoints(CLAIM), (Claim("c1", CLAIM),)
    )
    return matching_live_passages(CLAIM, result.passages)


class TwoQuoteModel:
    def __init__(self, *, only_first=False, malicious=False, missing_context=False):
        self.only_first = only_first
        self.malicious = malicious
        self.missing_context = missing_context
        self.calls = []

    async def complete_json(self, *, schema, prompt):
        data = json.loads(prompt)
        self.calls.append(data)
        passages = data["passages"]
        chosen = passages[:1] if self.only_first else passages
        return {
            "citations": [
                {"passage_id": item["passage_id"],
                 "quote": ("FAKE CITATION" if self.malicious else item["text"]),
                 "relation": "supports"}
                for item in chosen
            ],
            "missing_context": self.missing_context,
        }


@pytest.mark.asyncio
async def test_live_selector_finds_proposal_and_numeric_decrease_not_navigation():
    selected = await get_passages()
    assert len(selected) == 2
    assert any("przygotowało projekt" in p.exact_text for p in selected)
    assert any("196 km²" in p.exact_text and "0,99 km²" in p.exact_text for p in selected)
    assert all("Zobacz także" not in p.exact_text for p in selected)
    assert len({p.document_id for p in selected}) == 1
    assert all(CLAIM in p.selected_for for p in selected)
    assert all(p.exact_text in BODY for p in selected)


@pytest.mark.asyncio
async def test_joint_two_quote_llm_assessment_can_confirm():
    passages = await get_passages()
    model = TwoQuoteModel()
    actual = await EvidenceService(model).verify(Claim("c1", CLAIM), passages)
    assert actual.verdict is Verdict.CONFIRMED
    assert len(actual.citations) == 2
    assert len({p.passage.document_id for p in actual.citations}) == 1
    assert model.calls[0]["complementary_passage_groups"]


@pytest.mark.asyncio
async def test_one_part_does_not_confirm_full_claim():
    passages = await get_passages()
    actual = await EvidenceService(TwoQuoteModel(only_first=True)).verify(
        Claim("c1", CLAIM), passages
    )
    assert actual.verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_fabricated_quote_cannot_confirm():
    passages = await get_passages()
    actual = await EvidenceService(TwoQuoteModel(malicious=True)).verify(
        Claim("c1", CLAIM), passages
    )
    assert actual.verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_proposal_cannot_confirm_enactment_even_if_model_insists():
    passages = await get_passages()
    actual = await EvidenceService(TwoQuoteModel()).verify(
        Claim("c2", "Władze Krakowa już wprowadziły zmniejszoną SCT."), passages
    )
    assert actual.verdict is not Verdict.CONFIRMED


def test_selector_ignores_navigation_and_heading_on_realistic_document():
    selected, diagnostics = select(BODY, CLAIM)
    assert not diagnostics["missing_facets"]
    assert any("przygotowało projekt" in x.text for x in selected)
    assert any("196 km²" in x.text for x in selected)
    assert all("Zobacz także" not in x.text for x in selected)
    assert all(BODY[x.start:x.end] == x.text for x in selected)


def test_selector_refuses_wrong_metric_number():
    body = (
        "Program Boreal poprawił oceny ankiet o 30 procent.\n\n"
        "W ramach programu Boreal nie mierzono hospitalizacji. Nie ma danych pozwalających "
        "stwierdzić spadek liczby przyjęć do szpitala.\n\n"
        "Zobacz także: raport programu Boreal [pdf]"
    )
    selected, diagnostic = select(body, "Program Boreal zmniejszył liczbę hospitalizacji o 30 procent.")
    assert selected
    assert len(selected) == 1
    assert "nie mierzono hospitalizacji" in selected[0].text
    assert "number:30" in diagnostic["missing_facets"]


@pytest.mark.asyncio
async def test_pipeline_joined_evidence_delivers_two_citations():
    class ExtractAndVerify:
        async def extract(self, _text):
            return EventQuery(CLAIM), (Claim("c1", CLAIM, "verifiable", "public"),)

        async def verify(self, claim, passages):
            return await EvidenceService(TwoQuoteModel()).verify(claim, passages)

    class Retriever:
        async def search(self, _event, _query):
            return SimpleNamespace(passages=(), warnings=())

    class Discovery:
        async def discover(self, _event):
            return DiscoveryReport()

    class Corpus:
        async def enrich(self, _candidates):
            return False

    report = await AnalysisPipeline(
        ExtractAndVerify(), Retriever(), Discovery(), Corpus(),
        live_official=LiveOfficialEvidence(Web()),
    ).analyze("v31-sct", Submission("text", CLAIM, web_search=True))
    assert report["claims"][0]["verdict"] == "confirmed"
    assert len(report["claims"][0]["evidence_source_ids"]) == 2
    assert len({row["url"] for row in report["sources"]}) == 1


@pytest.mark.asyncio
async def test_citations_from_distinct_documents_cannot_form_one_proof():
    from dataclasses import replace

    pieces = await get_passages()
    assert len(pieces) == 2
    separated = (
        pieces[0],
        replace(pieces[1], document_id="live:another-document"),
    )
    outcome = await EvidenceService(TwoQuoteModel()).verify(
        Claim("c1", CLAIM), separated
    )
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
