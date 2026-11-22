"""Per-statement factuality, privacy, aliases and honest source coverage."""

import json
from dataclasses import dataclass

import pytest

from prawdowid.api.schemas import AnalysisReport
from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.domain.models import DocumentCandidate, EventQuery, EvidencePassage, Verdict
from prawdowid.domain.semantics import aliases_for, augmented_tokens, expand_query
from prawdowid.infrastructure.discovery import DiscoveryReport


class Model:
    def __init__(self, claims):
        self.claims = claims
        self.calls = []

    async def complete_json(self, *, schema, prompt):
        payload = json.loads(prompt)
        self.calls.append(payload)
        if payload["task"] == "extract":
            return {"claims": self.claims, "query_text": ""}
        assert payload["task"] == "assess", "No evidence means verification must abstain"
        statement = payload["statement"]
        if statement == "SCT to fatalny pomysł.":
            return {"kind": "opinion", "scope": "unknown", "reason": "Subiektywna ocena wartościująca."}
        if statement == "Strefa czystego transportu":
            return {"kind": "non_claim", "scope": "unknown", "reason": "Sama nazwa tematu bez twierdzenia."}
        if statement == "Moja krowa daje mleko.":
            return {"kind": "verifiable", "scope": "private", "reason": "Fakt indywidualny o zwierzęciu.", "empirical_test": "Sprawdzenie laktacji konkretnej krowy."}
        return {"kind": "verifiable", "scope": "public", "reason": "Można sprawdzić zdarzenie w dokumentacji.", "empirical_test": "Sprawdzić dokumentację, datę oraz komunikat dotyczący zdarzenia."}


class Retrieval:
    def __init__(self):
        self.calls = []

    async def search(self, event, text):
        self.calls.append((event, text))

        @dataclass
        class Result:
            passages: tuple = ()
            warnings: tuple = ()

        return Result()


class Discovery:
    def __init__(self, candidates=()):
        self.candidates = candidates
        self.calls = []

    async def discover(self, event):
        self.calls.append(event)
        return DiscoveryReport(candidates=self.candidates)


class Corpus:
    def __init__(self):
        self.calls = 0

    async def enrich(self, candidates):
        self.calls += 1
        return False


@pytest.mark.asyncio
async def test_krakow_statement_is_investigable_when_model_says_nonclaim():
    text = "Władze Krakowa zaproponowały zmniejszenie strefy czystego transportu."
    model = Model([{"text": text, "kind": "non_claim"}])
    event, claims = await EvidenceService(model).extract(text)
    assert event.query_text == text
    assert len(claims) == 1
    assert claims[0].kind == "verifiable"
    verdict = await EvidenceService(model).verify(claims[0], [])
    assert verdict.verdict is Verdict.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_private_animal_does_not_become_publicly_verified_by_generic_biology():
    statement = "Moja krowa daje mleko."
    service = EvidenceService(Model([{"text": statement, "kind": "non_claim"}]))
    _, claims = await service.extract(statement)
    assert claims[0].kind == "verifiable"
    unrelated = EvidencePassage(
        "p1", "d1", "Krowy mogą produkować mleko.", "Biologia", "Krowy",
        "https://example.org/biology", None,
    )
    verdict = await service.verify(claims[0], [unrelated])
    assert verdict.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert verdict.citations == ()
    assert "konkretnej" in verdict.rationale


@pytest.mark.asyncio
async def test_two_claims_remain_independent_with_explicit_spans_and_private_scope():
    public = "Władze Krakowa zaproponowały zmniejszenie SCT."
    private = "Moja krowa daje mleko."
    text = f"{public} {private}"
    model = Model([])
    retrieval, discovery, corpus = Retrieval(), Discovery(), Corpus()
    report = await AnalysisPipeline(EvidenceService(model), retrieval, discovery, corpus).analyze(
        "example", Submission("text", text, web_search=True)
    )
    assert len(report["claims"]) == 2
    assert [claim["verdict"] for claim in report["claims"]] == [
        "insufficient_evidence", "insufficient_evidence",
    ]
    assert report["claims"][0]["aliases"] == ["SCT", "strefa czystego transportu"]
    assert report["claims"][1]["evidence_scope"] == "private"
    assert report["claims"][1]["assumptions"]
    for claim in report["claims"]:
        assert text[claim["span_start"]:claim["span_end"]] == claim["text"]
    assert len(retrieval.calls) == 1  # No public search for the cow.
    assert "strefa czystego transportu" in retrieval.calls[0][1]
    assert report["search"]["requested"] and report["search"]["attempted"]
    assert len(discovery.calls) == 1
    AnalysisReport.model_validate(report)


@pytest.mark.asyncio
async def test_private_only_never_goes_to_news_provider_even_with_optin():
    statement = "Moja krowa daje mleko."
    model = Model([])
    retrieval, discovery, corpus = Retrieval(), Discovery(), Corpus()
    report = await AnalysisPipeline(EvidenceService(model), retrieval, discovery, corpus).analyze(
        "example", Submission("text", statement, web_search=True)
    )
    assert discovery.calls == []
    assert retrieval.calls == []
    assert report["search"]["status"] == "skipped_no_public_claims"
    assert not report["search"]["attempted"]
    assert report["claims"][0]["verdict"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_factcheck_discovery_is_not_accepted_as_evidence():
    claim = "Władze Krakowa zaproponowały zmniejszenie SCT."
    candidate = DocumentCandidate(
        "https://demagog.org.pl/fake_news/claim", "Demagog", "SCT - analiza", None, "rss"
    )
    model = Model([])
    discovery = Discovery((candidate,))
    report = await AnalysisPipeline(
        EvidenceService(model), Retrieval(), discovery, Corpus()
    ).analyze("example", Submission("text", claim, web_search=True))
    assert report["sources"] == []
    assert report["claims"][0]["verdict"] == "insufficient_evidence"
    assert report["coverage"]["fact_check_candidates"][0]["category"] == "fact_check"
    assert report["coverage"]["evidence_publishers"] == []
    assert report["search"]["candidates_found"] == 1


def test_contextual_aliases_expand_search_but_do_not_rewrite_the_statement():
    assert aliases_for("W Krakowie zmienią SCT.") == ("SCT", "strefa czystego transportu")
    assert aliases_for("Strefa Czystego Transportu w Krakowie") == (
        "SCT", "strefa czystego transportu"
    )
    assert "strefa czystego transportu" in expand_query("W Krakowie zmienią SCT.")
    assert "sct" in augmented_tokens(["strefa", "czystego", "transportu"], "strefa czystego transportu")
    assert aliases_for("Moja krowa daje mleko.") == ()


@pytest.mark.asyncio
async def test_opinion_and_topic_do_not_turn_into_claims():
    model = Model([])
    service = EvidenceService(model)
    for sentence in ("SCT to fatalny pomysł.", "Strefa czystego transportu"):
        _, claims = await service.extract(sentence)
        assert all(claim.kind != "verifiable" for claim in claims)

@pytest.mark.asyncio
async def test_distinct_public_sentences_get_distinct_searches_and_link_attribution():
    first = "Władze Krakowa zaproponowały zmniejszenie SCT."
    second = "W Warszawie otwarto nowy most."
    discovery = Discovery((DocumentCandidate(
        "https://example.org/overview", "Media", "Informacje", None, "rss"
    ),))
    report = await AnalysisPipeline(
        EvidenceService(Model([])), Retrieval(), discovery, Corpus()
    ).analyze("example", Submission("text", f"{first} {second}", web_search=True))
    assert len(discovery.calls) == 2
    assert discovery.calls[0].query_text == first
    assert discovery.calls[1].query_text == second
    assert len(report["discovered_links"]) == 1
    assert report["discovered_links"][0]["claim_ids"] == ["c1", "c2"]
    assert report["claims"][0]["topic_links_count"] == 1
    assert report["claims"][1]["topic_links_count"] == 1
    assert len(report["search"]["per_claim"]) == 2
    AnalysisReport.model_validate(report)


def test_sct_event_alias_does_not_make_proposal_an_implementation():
    pytest.importorskip("pgvector")
    from prawdowid.infrastructure.retrieval import RetrievedPassage, matches_event

    def candidate(text):
        return RetrievedPassage(
            passage_id="p", document_id="d", exact_text=text,
            publisher="Wydawca", title="Wiadomość o SCT Krakowa",
            url="https://example.org/a", published_at=None,
            fetched_at=None, content_version=1, char_start=0, char_end=len(text),
            source_span_hash="hash", source_domain="example.org", source_type="news",
        )

    query = EventQuery(
        "Władze Krakowa zaproponowały zmniejszenie SCT.", entities=("Krakowa",)
    )
    assert matches_event(query, candidate(
        "Władze Krakowa proponują zmniejszenie strefy czystego transportu."
    ))
    assert not matches_event(query, candidate(
        "W Krakowie obowiązuje strefa czystego transportu i rozpoczęła działalność."
    ))


@pytest.mark.asyncio
async def test_general_biology_statement_is_factually_investigable_not_personal_proof():
    general = "Krowy dają mleko."
    event, claims = await EvidenceService(Model([])).extract(general)
    assert event.query_text == general
    assert claims[0].kind == "verifiable"
    report = await AnalysisPipeline(
        EvidenceService(Model([])), Retrieval(), Discovery(), Corpus()
    ).analyze("example", Submission("text", general))
    assert report["claims"][0]["evidence_scope"] == "public"
    assert report["claims"][0]["verdict"] == "insufficient_evidence"
