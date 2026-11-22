"""Regression for long editorial articles, low model recall and evidence honesty."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from prawdowid.api.schemas import AnalysisReport
from prawdowid.application import claim_batches as batches_module
from prawdowid.application import claims as claims_module
from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.infrastructure.safe_web import extract_article

REALISTIC_HTML = """<html><head><title>Strefa Czystego Transportu — projekt</title></head>
<body>
<article><p>To jest jedynie zapowiedź innego artykułu.</p></article>
<main><article><div itemprop="articleBody">
<p>Miasto proponuje zmniejszenie strefy z 196 km kwadratowych do 0,99 km kwadratowego.</p>
<p>Rozszerzenie zwolnień dla mieszkańców zapisano w projekcie uchwały.</p>
<ul>
<li>Projekt zakłada rezygnację z obowiązkowych nalepek na szybach samochodów.</li>
<li>Wysokość obecnych opłat pozostałaby na dotychczasowym poziomie.</li>
<li><p>Projekt wymaga jeszcze dalszych konsultacji i nie jest obowiązującym prawem.</p></li>
</ul>
<p>W 2024 roku odnotowano przekroczenie poziomu dwutlenku azotu przy alei Krasińskiego.</p>
<p>Poprawa jakości powietrza rozpoczęła się jeszcze przed wprowadzeniem nowych przepisów.</p>
</div></article></main>
<aside><p>Polecane artykuły i reklamy, których nie należy analizować.</p></aside>
</body></html>""".encode()


class UnderselectingModel:
    def __init__(self):
        self.extractions = []
        self.assessments = []

    async def complete_json(self, *, schema, prompt):
        payload = json.loads(prompt)
        if payload["task"] == "extract":
            text = payload["input_text"]
            self.extractions.append(text)
            first = claims_module.candidate_spans(text)[0]
            return {"claims": [{"text": first, "kind": "verifiable", "scope": "public"}]}
        if payload["task"] == "assess":
            self.assessments.append(payload["statement"])
            return {
                "kind": "verifiable", "scope": "public",
                "reason": "Tezę można porównać z dokumentem urzędowym.",
                "empirical_test": "Porównać z aktualnym tekstem uchwały.",
            }
        raise AssertionError("Evidence model must not run without source passages")


class EmptyRetriever:
    async def search(self, event, query):
        return SimpleNamespace(passages=(), warnings=())


class NoDiscovery:
    async def discover(self, event):
        raise AssertionError("No remote lookup in this regression")


class NoCorpus:
    async def article(self, url):
        raise AssertionError("No network retrieval in this regression")


def test_html_selects_main_article_not_teaser_and_includes_lists():
    document = extract_article(REALISTIC_HTML, allow_text=True)
    assert document.body is not None
    assert "Miasto proponuje" in document.body
    assert "obowiązkowych nalepek" in document.body
    assert "dalszych konsultacji" in document.body
    assert "2024 roku" in document.body
    assert "zapowiedź innego" not in document.body
    assert "Polecane artykuły" not in document.body
    assert len(claims_module.candidate_spans(document.body)) >= 7


@pytest.mark.asyncio
async def test_model_underselects_but_pipeline_assesses_all_seven_exact_statements():
    document = extract_article(REALISTIC_HTML, allow_text=True)
    model = UnderselectingModel()
    text = document.body
    result = await AnalysisPipeline(
        EvidenceService(model), EmptyRetriever(), NoDiscovery(), NoCorpus()
    ).analyze("test-v19", Submission("text", text))
    assert len(model.extractions) == 2
    assert len(result["claims"]) >= 6
    assert len(result["claims"]) <= 10
    assert all(claim["classification_reviewed"] for claim in result["claims"])
    assert all(claim["verdict"] == "insufficient_evidence" for claim in result["claims"])
    assert result["coverage"]["input_chars"] == len(text)
    assert result["coverage"]["cited_claims"] == 0
    assert result["coverage"]["partial_analysis"] is True
    assert any("Nie zweryfikowano żadnej" in note for note in result["limitations"])
    for claim in result["claims"]:
        assert text[claim["span_start"]:claim["span_end"]] == claim["text"]
    validated = AnalysisReport.model_validate(result)
    assert validated.coverage.evidence_by_category["official"] == []
    assert "major_media" in validated.coverage.evidence_categories_missing


@pytest.mark.asyncio
async def test_twelve_assertions_show_explicit_partial_coverage_and_no_fake_citations():
    text = "\n".join(
        f"Miejska uchwała wymienia rozwiązanie numer {i} dotyczące transportu publicznego."
        for i in range(1, 13)
    )
    report = await AnalysisPipeline(
        EvidenceService(UnderselectingModel()), EmptyRetriever(), NoDiscovery(), NoCorpus()
    ).analyze("test-v19-limit", Submission("text", text))
    assert len(report["claims"]) <= 10
    assert report["coverage"]["input_segments"] == 12
    assert report["coverage"]["partial_analysis"]
    assert all(not claim["evidence_source_ids"] for claim in report["claims"])
    assert any("Nie wszystkie segmenty" in note for note in report["limitations"])


def test_article_length_but_no_terminal_punctuation_batches_by_paragraph():
    text = "\n\n".join(
        f"Uchwała wprowadza rozwiązanie dotyczące kategorii numer {i} i dodatkowych wyjątków"
        for i in range(1, 7)
    )
    assert len(batches_module.claim_batches(text)) == 2


def test_regression_ui_never_calls_uncited_report_complete():
    js = (Path(__file__).resolve().parents[1] / "src/prawdowid/web/app.js").read_text()
    assert "Analiza częściowa — sprawdź pokrycie" in js
    assert "Pokrycie materiału:" in js


def test_main_fallback_beats_single_teaser_without_articlebody_selector():
    html = """<article><p>Krótka zapowiedź.</p></article><main>
    <p>Strefa Czystego Transportu pozostaje przedmiotem konsultacji w miejskim urzędzie.</p>
    <p>Proponowane wyjątki obejmują dodatkowe grupy mieszkańców i użytkowników dróg.</p>
    <p>Projekt zmian przewiduje również inny sposób sprawdzania uprawnień pojazdów.</p>
    <p>Wysokość opłat i terminy ich pobierania mają pozostać niezmienione.</p>
    </main>""".encode()
    result = extract_article(html, allow_text=True)
    assert "przedmiotem konsultacji" in result.body
    assert "opłat" in result.body
    assert "Krótka zapowiedź" not in result.body
