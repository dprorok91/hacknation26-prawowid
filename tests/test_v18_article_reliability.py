"""V18 regressions: bounded article coverage, canonical citations and model diagnostics."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from prawdowid.application.analyses import Submission
from prawdowid.application.claim_batches import claim_batches
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline, _merge_retrieval
from prawdowid.domain.models import Claim, EventQuery, EvidencePassage
from prawdowid.domain.urls import canonical_url


class BatchModel:
    def __init__(self):
        self.inputs = []

    async def complete_json(self, *, schema, prompt):
        payload = json.loads(prompt)
        if payload["task"] == "extract":
            self.inputs.append(payload["input_text"])
            return {"claims": []}
        if payload["task"] == "assess":
            return {
                "kind": "verifiable", "scope": "public",
                "empirical_test": "Sprawdzić oficjalne dane w dokumentacji.",
                "reason": "Publicznie weryfikowalne twierdzenie w dokumentacji.",
            }
        raise AssertionError("No model verification without retrieved passages")


@dataclass
class SearchResults:
    passages: tuple = ()
    warnings: tuple = ()


class SpyRetrieval:
    def __init__(self):
        self.queries = []

    async def search(self, event, query):
        self.queries.append(query)
        return SearchResults()


class NoDiscovery:
    async def discover(self, event):
        raise AssertionError("Offline search must stay disabled")


class NoCorpus:
    async def article(self, url):
        raise AssertionError("Text analysis must not fetch the submitted URL")


@pytest.mark.asyncio
async def test_eight_sentences_are_analyzed_in_two_batches_with_original_offsets():
    sentences = [f"W Krakowie ogłoszono decyzję numer {i}." for i in range(1, 9)]
    text = " ".join(sentences)
    model = BatchModel()
    retrieval = SpyRetrieval()
    result = await AnalysisPipeline(
        EvidenceService(model), retrieval, NoDiscovery(), NoCorpus()
    ).analyze("v18", Submission("text", text))
    assert len(model.inputs) == 2
    assert len(result["claims"]) == 8
    assert [claim["claim_id"] for claim in result["claims"]] == [f"c{i}" for i in range(1, 9)]
    assert all(
        text[claim["span_start"]:claim["span_end"]] == claim["text"]
        for claim in result["claims"]
    )
    assert any("maksymalnie 10" in note for note in result["limitations"])


def test_short_text_does_not_trigger_multiple_provider_calls():
    assert claim_batches("To pierwsza teza. To druga teza.") == (
        "To pierwsza teza. To druga teza.",
    )


def test_canonical_url_drops_empty_utm_but_preserves_resource_identifier():
    assert canonical_url("https://www.bip.krakow.pl/?dok_id=226666&utm=") == (
        "https://www.bip.krakow.pl/?dok_id=226666"
    )
    assert canonical_url("https://example.org/?page=2&utm_source=example") == (
        "https://example.org/?page=2"
    )


@pytest.mark.asyncio
async def test_absolute_claim_searches_local_counter_context():
    model = BatchModel()
    retrieval = SpyRetrieval()
    pipeline = AnalysisPipeline(EvidenceService(model), retrieval, NoDiscovery(), NoCorpus())
    claim = Claim("c1", "Wszystkie pojazdy są zakazane w Krakowie.", "verifiable", "public")
    results = await pipeline._retrieve(EventQuery(claim.text), (claim,))
    assert len(retrieval.queries) == 2
    assert "wyjątki" in retrieval.queries[1]
    assert results["c1"].passages == ()


def test_url_failure_fallback_is_explicit_and_does_not_auto_enable_web_search():
    path = Path(__file__).resolve().parents[1] / "src" / "prawdowid" / "web"
    html = (path / "index.html").read_text()
    js = (path / "app.js").read_text()
    assert 'id="paste-article-fallback"' in html
    assert 'webSearch.checked = false;' in js
    assert 'code === "source_unavailable"' in js
    assert "pasteFallback.addEventListener" in js

@pytest.mark.asyncio
async def test_model_schema_failure_logs_no_user_content(caplog):
    class InvalidModel:
        async def complete_json(self, *, schema, prompt):
            return {"claims": "secret-statement-sentinel"}

    from prawdowid.application.analyses import AnalysisError, ErrorCode

    with pytest.raises(AnalysisError) as failure:
        await EvidenceService(InvalidModel()).extract("User private sentinel statement.")
    assert failure.value.code == ErrorCode.MODEL_UNAVAILABLE
    assert "_Extraction" in caplog.text
    assert "sentinel" not in caplog.text


def test_counter_evidence_is_not_lost_when_primary_results_are_full():
    def passage(i):
        return EvidencePassage(
            f"passage-{i}", f"doc-{i}", "Treść źródła", "BIP", "Tytuł",
            "https://example.org/", None,
        )

    primary = SearchResults(passages=tuple(passage(i) for i in range(10)))
    other = SearchResults(passages=tuple(passage(i) for i in range(10, 15)))
    result = _merge_retrieval(primary, other)
    assert len(result.passages) == 10
    assert [row.passage_id for row in result.passages[:6]] == [
        f"passage-{i}" for i in range(6)
    ]
    assert {f"passage-{i}" for i in range(10, 14)} <= {
        row.passage_id for row in result.passages
    }


def test_merge_retrieval_supports_plain_object_test_doubles():
    class Empty:
        passages = ()
        warnings = ("vector_search_disabled",)

    result = _merge_retrieval(Empty(), Empty())
    assert result.passages == ()
    assert result.warnings == ("vector_search_disabled",)
