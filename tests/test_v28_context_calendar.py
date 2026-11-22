"""V28 regressions for anaphoric claims, Polish forms and exact calendar rules."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prawdowid.application.evidence import (
    EvidenceService,
    _calendar_check,
    _has_claim_anchors,
)
from prawdowid.application.pipeline import AnalysisPipeline, _article_referents
from prawdowid.domain.models import Claim, EventQuery, EvidencePassage, Verdict
from prawdowid.infrastructure.retrieval import RetrievedPassage, matches_event

EVENT = "Światowy Dzień Pamięci Ofiar Wypadków Drogowych"
QUOTE = (
    "Co roku w trzecią niedzielę listopada obchodzony jest "
    "Światowy Dzień Pamięci o Ofiarach Wypadków Drogowych, "
    "zainicjowany w 1993 r."
)
F3 = "Obchody tego dnia zainicjowano w 1993 roku."
B2 = "W 2026 roku datą obchodów był 1 listopada 2026 roku."


def item(identifier="p", text=QUOTE):
    return RetrievedPassage(
        passage_id=identifier,
        document_id="gov-document",
        exact_text=text,
        publisher="Portal Gov.pl",
        title="Światowy Dzień Pamięci",
        url="https://www.gov.pl/web/infrastruktura/news",
        published_at=None,
        fetched_at=datetime(2026, 11, 22, tzinfo=UTC),
        content_version=1,
        char_start=0,
        char_end=len(text),
        source_span_hash="sample",
        source_domain="www.gov.pl",
        source_type="primary",
    )


class FixedRelationModel:
    def __init__(self, relation):
        self.relation = relation
        self.payload = None

    async def complete_json(self, *, schema, prompt):
        self.payload = json.loads(prompt)
        return {"citations": [{
            "passage_id": "p", "quote": QUOTE, "relation": self.relation,
        }]}


def test_1993_quote_with_polish_inflections_is_relevant():
    assert _has_claim_anchors(F3, QUOTE)
    assert _has_claim_anchors(f"{EVENT}. {F3}", QUOTE)
    assert matches_event(EventQuery(query_text=F3), item())


@pytest.mark.asyncio
async def test_verified_1993_paraphrase_requires_matching_number():
    model = FixedRelationModel("supports")
    subject_claim = Claim("c1", f"{EVENT}. {F3}")
    decision = await EvidenceService(model).verify(
        subject_claim,
        [EvidencePassage("p", "doc", QUOTE, "Gov", "News", "https://www.gov.pl/a", None)],
    )
    assert decision.verdict is Verdict.CONFIRMED
    assert len(decision.citations) == 1

    unrelated = QUOTE.replace("1993", "1994")

    class OtherModel:
        async def complete_json(self, *, schema, prompt):
            return {"citations": [{
                "passage_id": "p", "quote": unrelated, "relation": "supports",
            }]}

    outcome = await EvidenceService(OtherModel()).verify(
        subject_claim,
        [EvidencePassage("p", "doc", unrelated, "Gov", "News", "https://www.gov.pl/a", None)],
    )
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE


def test_referents_are_adjacent_and_stay_in_same_paragraph():
    f2 = f"{EVENT} obchodzony jest co roku w trzecią niedzielę listopada."
    b1 = f"{EVENT} przypada w pierwszą niedzielę listopada."
    text = f"{f2} {F3}\n\n{b1} {B2}"
    claims = tuple(Claim(f"c{index + 1}", line) for index, line in enumerate((f2, F3, b1, B2)))
    assert _article_referents(text, claims) == {"c2": EVENT, "c4": EVENT}
    assert _article_referents(f"{f2}\n\n{F3}", claims[:2]) == {}
    assert _article_referents(f"Dane urzędu. {F3}", (Claim("a", "Dane urzędu."), claims[1])) == {}


class SearchRecorder:
    def __init__(self):
        self.queries = []

    async def search(self, event, query):
        self.queries.append(query)
        if EVENT in query:
            return SimpleNamespace(passages=(item(),), warnings=())
        return SimpleNamespace(passages=(), warnings=("embedding_unavailable",))


@pytest.mark.asyncio
async def test_anaphoric_claim_adds_bounded_contextual_local_query():
    retriever = SearchRecorder()
    pipeline = AnalysisPipeline(None, retriever, None, None)
    claim = Claim("c3", F3, "verifiable", "public")
    output = await pipeline._retrieve(
        EventQuery(query_text=""), (claim,), referents={"c3": EVENT}
    )
    assert [p.passage_id for p in output["c3"].passages] == ["p"]
    assert len(retriever.queries) == 2
    assert retriever.queries[1].startswith(EVENT)
    assert len(output["c3"].passages) == 1


def test_calendar_derivation_only_for_named_celebration_dates():
    claim = f"{EVENT}. {B2}"
    assert _calendar_check(claim, QUOTE) == {
        "claimed_date": "2026-11-01",
        "rule_implied_date": "2026-11-15",
        "conflicts": True,
    }
    assert _calendar_check(
        "15 listopada 2026 ministerstwo opublikowało informację o obchodach.", QUOTE
    ) is None
    assert _calendar_check(claim, "Obchody trwają w listopadzie.") is None
    assert _calendar_check(claim, QUOTE.replace("Co roku ", "")) is None
    assert _calendar_check(claim, QUOTE.replace("listopada", "grudnia")) is None


@pytest.mark.asyncio
async def test_calendar_derivation_visible_to_model_without_fabricating_citation():
    model = FixedRelationModel("refutes")
    claim = Claim("c5", f"{EVENT}. {B2}")
    decision = await EvidenceService(model).verify(
        claim, [EvidencePassage("p", "d", QUOTE, "Gov", "News", "https://www.gov.pl/a", None)]
    )
    assert model.payload["passages"][0]["text"] == QUOTE
    assert model.payload["passages"][0]["calendar_check"]["conflicts"] is True
    assert decision.verdict is Verdict.FALSE


@pytest.mark.asyncio
async def test_unrelated_outcome_cannot_use_calendar_quote_to_refute():
    model = FixedRelationModel("refutes")
    claim = Claim("c6", "Urzędnicy całkowicie rozwiązali problem wypadków drogowych.")
    decision = await EvidenceService(model).verify(
        claim, [EvidencePassage("p", "d", QUOTE, "Gov", "News", "https://www.gov.pl/a", None)]
    )
    assert decision.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert model.payload["passages"][0]["calendar_check"] is None


@pytest.mark.asyncio
async def test_calendar_quote_cannot_support_opposite_date_or_refute_correct_date():
    contradicted = Claim("c", f"{EVENT}. {B2}")
    right_date = Claim(
        "c", f"{EVENT}. W 2026 roku datą obchodów był 15 listopada 2026 roku."
    )
    passage = EvidencePassage(
        "p", "d", QUOTE, "Gov", "News", "https://www.gov.pl/a", None
    )
    wrong_support = await EvidenceService(FixedRelationModel("supports")).verify(
        contradicted, [passage]
    )
    wrong_refutation = await EvidenceService(FixedRelationModel("refutes")).verify(
        right_date, [passage]
    )
    assert wrong_support.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert wrong_refutation.verdict is Verdict.INSUFFICIENT_EVIDENCE
