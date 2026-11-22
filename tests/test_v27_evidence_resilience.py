"""V27 regressions: bounded model output, grounded quotes and source provenance."""

import json
import logging
from types import SimpleNamespace

import pytest

from prawdowid.application.analyses import AnalysisError, ErrorCode, Submission
from prawdowid.application.evidence import EvidenceService, _Extraction
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.domain.models import Claim, EvidencePassage, Verdict


class SequenceModel:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    async def complete_json(self, *, schema, prompt):
        self.calls.append(json.loads(prompt))
        response = next(self.answers)
        if isinstance(response, Exception):
            raise response
        return response


def passage(identifier, url, quote, *, publisher="Wrong ministry"):
    return EvidencePassage(identifier, identifier, quote, publisher, "Strona", url, None)


@pytest.mark.asyncio
async def test_extraction_bounds_provider_oversized_array_without_failing_analysis():
    model = SequenceModel([{"claims": [
        {"text": f"Teza numer {number}.", "kind": "verifiable"}
        for number in range(7)
    ]}])
    result = await EvidenceService(model)._complete(
        _Extraction, {"task": "extract", "input_text": "Przykładowy tekst."}
    )
    assert len(result.claims) == 5
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_invalid_extraction_gets_one_bounded_schema_retry():
    model = SequenceModel([
        {"claims": "invalid"},
        {"claims": [{"text": "Poprawne twierdzenie.", "kind": "verifiable"}]},
    ])
    result = await EvidenceService(model)._complete(
        _Extraction, {"task": "extract", "input_text": "Poprawne twierdzenie."}
    )
    assert len(result.claims) == 1
    assert len(model.calls) == 2
    assert "schema_retry" not in model.calls[0]
    assert "schema_retry" in model.calls[1]


@pytest.mark.asyncio
async def test_repeated_invalid_schema_abstains_and_does_not_log_user_data(caplog):
    model = SequenceModel([
        {"claims": "private-source-SENTINEL"},
        {"claims": "private-source-SENTINEL"},
    ])
    with caplog.at_level(logging.WARNING), pytest.raises(AnalysisError) as failure:
        await EvidenceService(model)._complete(
            _Extraction, {"task": "extract", "input_text": "private-user-SENTINEL"}
        )
    assert failure.value.code is ErrorCode.MODEL_UNAVAILABLE
    assert len(model.calls) == 2
    assert "_Extraction" in caplog.text
    assert "SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_model_provider_failure_is_not_retried():
    failure = AnalysisError(ErrorCode.PROVIDER_RATE_LIMITED, "rate limited")
    model = SequenceModel([failure])
    with pytest.raises(AnalysisError) as error:
        await EvidenceService(model)._complete(_Extraction, {"task": "extract"})
    assert error.value.code is ErrorCode.PROVIDER_RATE_LIMITED
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_event_calendar_quote_cannot_refute_unrelated_safety_outcome():
    text = (
        "Komunikat o Światowym Dniu Pamięci Ofiar Wypadków Drogowych "
        "dowodzi, że problem wypadków drogowych w Polsce został całkowicie rozwiązany."
    )
    quote = (
        "Co roku w trzecią niedzielę listopada obchodzony jest "
        "Światowy Dzień Pamięci o Ofiarach Wypadków Drogowych."
    )
    model = SequenceModel([{"citations": [{
        "passage_id": "p1", "quote": quote, "relation": "refutes"
    }]}])
    outcome = await EvidenceService(model).verify(
        Claim("c1", text), [passage("p1", "https://www.gov.pl/web/infrastruktura", quote)]
    )
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert outcome.citations == ()


@pytest.mark.asyncio
async def test_citation_without_claimed_year_cannot_confirm():
    claim = "W 1993 roku w Warszawie otwarto 12 szkół."
    quote = "W Warszawie otwarto 12 szkół."
    model = SequenceModel([{"citations": [{
        "passage_id": "p1", "quote": quote, "relation": "supports"
    }]}])
    decision = await EvidenceService(model).verify(
        Claim("c1", claim), [passage("p1", "https://www.gov.pl/web/gov", quote)]
    )
    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_explicit_counterexample_may_have_different_number():
    claim = "W Warszawie było 12 szkół."
    quote = "W Warszawie było 10 szkół."
    model = SequenceModel([{"citations": [{
        "passage_id": "p1", "quote": quote, "relation": "refutes"
    }]}])
    result = await EvidenceService(model).verify(
        Claim("c1", claim), [passage("p1", "https://example.org/document", quote)]
    )
    assert result.verdict is Verdict.FALSE


class ArticleModel:
    async def complete_json(self, *, schema, prompt):
        payload = json.loads(prompt)
        if payload["task"] == "extract":
            return {"claims": [{"text": payload["input_text"], "kind": "verifiable"}]}
        if payload["task"] == "assess":
            return {
                "kind": "verifiable", "scope": "public",
                "reason": "Dane weryfikowalne w dokumentacji publicznej.",
                "empirical_test": "Porównać z rejestrem urzędu i jego datami.",
            }
        if payload["task"] == "verify":
            return {"citations": [
                {"passage_id": entry["passage_id"], "quote": entry["text"],
                 "relation": "supports"}
                for entry in payload["passages"]
            ]}
        raise AssertionError("Unexpected model task")


class LocalRetriever:
    def __init__(self, results):
        self.results = results

    async def search(self, event, query):
        return SimpleNamespace(passages=self.results, warnings=())


class Offline:
    async def discover(self, event):
        raise AssertionError("Online lookup must remain disabled")


@pytest.mark.asyncio
async def test_duplicate_govpl_quote_is_single_citation_with_neutral_publisher():
    claim = "W Warszawie było 12 szkół."
    pages = (
        passage("p1", "https://www.gov.pl/web/infrastruktura/page-a", claim),
        passage("p2", "https://www.gov.pl/web/mswia/page-b", claim),
    )
    report = await AnalysisPipeline(
        EvidenceService(ArticleModel()), LocalRetriever(pages), Offline(), None,
    ).analyze("v27", Submission("text", claim))
    assert report["claims"][0]["verdict"] == "confirmed"
    assert len(report["claims"][0]["evidence_source_ids"]) == 1
    assert len(report["sources"]) == 1
    assert report["sources"][0]["publisher"] == "Portal Gov.pl"
    assert report["coverage"]["evidence_publishers"] == ["Portal Gov.pl"]
