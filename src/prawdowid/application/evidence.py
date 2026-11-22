"""Extract claims and apply conservative verdicts to grounded model judgments."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.application.claims import sentence_spans
from prawdowid.domain.models import Claim, EventQuery, EvidencePassage, EvidenceRelation, Verdict
from prawdowid.domain.ports import StructuredLLM
from prawdowid.domain.urls import eligible_model_url

Output = TypeVar("Output", bound=BaseModel)

PASSAGE_LIMIT = 10
PASSAGE_CHARACTERS = 2000
INPUT_CHARACTERS = 18000
_CLAIM_CHARACTERS = 400
_ANCHOR_WORDS = re.compile(r"[^\W_]+")
_CONNECTORS = {"jest", "była", "było", "które", "który", "oraz", "według", "także"}
_PROMPT_INSTRUCTIONS = re.compile(
    r"\b(?:ignore|disregard|override|zignoruj|ignoruj|pomiń)\b.{0,60}"
    r"(?:instructions?|prompt|instrukcj|polecen)",
    re.IGNORECASE | re.DOTALL,
)


class _ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class _Proposition(_ModelOutput):
    text: Annotated[str, Field(min_length=1, max_length=_CLAIM_CHARACTERS)]
    kind: Literal["verifiable", "opinion", "non_claim", "unsupported_scope"]
    scope: Literal["public", "private", "unknown"] = "unknown"
    empirical_test: str = Field(default="", max_length=500)
    reason: str = Field(default="", max_length=500)


class _Assessment(_ModelOutput):
    kind: Literal["verifiable", "opinion", "non_claim", "unsupported_scope"]
    scope: Literal["public", "private", "unknown"]
    empirical_test: str = Field(default="", max_length=500)
    reason: str = Field(min_length=8, max_length=500)


class _Extraction(_ModelOutput):
    claims: list[_Proposition] = Field(max_length=5)
    query_text: str = Field(default="", max_length=400)
    entities: list[Annotated[str, Field(max_length=100)]] = Field(
        default_factory=list, max_length=8
    )
    location: str | None = Field(default=None, max_length=100)
    date_from: date | None = None
    date_to: date | None = None


class _ModelCitation(_ModelOutput):
    passage_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=4, max_length=500)
    relation: EvidenceRelation


class _Judgment(_ModelOutput):
    citations: list[_ModelCitation] = Field(max_length=8)
    missing_context: bool = Field(default=False, strict=True)


@dataclass(frozen=True)
class EvidenceCitation:
    passage: EvidencePassage
    quote: str
    relation: EvidenceRelation


@dataclass(frozen=True)
class Verification:
    verdict: Verdict
    rationale: str
    citations: tuple[EvidenceCitation, ...] = ()


class EvidenceService:
    def __init__(self, model: StructuredLLM, *, public_sources_only: bool = False):
        self.model = model
        self.public_sources_only = public_sources_only

    async def extract(self, text: str) -> tuple[EventQuery, tuple[Claim, ...]]:
        text = text.strip()[:INPUT_CHARACTERS]
        if not text:
            return _nonclaim(text)
        result = await self._complete(_Extraction, {"task": "extract", "input_text": text})
        selected: dict[str, _Proposition] = {}
        for item in result.claims:
            wording = item.text.strip()
            if wording and wording in text and not _PROMPT_INSTRUCTIONS.search(wording):
                selected.setdefault(wording, item)

        # Segmentation only. Punctuation, capitalization and keywords do not
        # decide factuality, and no word-count threshold is used as a gate.
        if not selected:
            for sentence, _, _ in sentence_spans(text):
                if not _PROMPT_INSTRUCTIONS.search(sentence):
                    selected[sentence] = _Proposition(text=sentence, kind="unsupported_scope")

        # One short sentence is an indivisible assertion: dropping a qualifier,
        # negation, date or exception would change what we subsequently verify.
        if len(selected) == 1 and "\n" not in text and len(text) <= _CLAIM_CHARACTERS:
            spans = sentence_spans(text)
            if len(spans) == 1:
                sentence = spans[0][0]
                wording, item = next(iter(selected.items()))
                if wording != sentence and wording in sentence and not _PROMPT_INSTRUCTIONS.search(sentence):
                    selected = {sentence: item}

        claims = []
        for wording, item in list(selected.items())[:5]:
            kind, scope, empirical_test, reason = (
                item.kind, item.scope, item.empirical_test, item.reason
            )
            reviewed = False
            # Independently review every proposed class, including a false
            # positive "verifiable" for a subjective judgment. This avoids
            # rewarding whichever class a stochastic extractor returned first.
            reviewed = True
            try:
                assessment = await self._complete(
                    _Assessment, {"task": "assess", "statement": wording}
                )
                kind, scope, empirical_test, reason = (
                    assessment.kind, assessment.scope,
                    assessment.empirical_test, assessment.reason,
                )
                # A yes/no label without an observable falsification test
                # is not enough to launch a public-source search.
                if kind == "verifiable" and not empirical_test.strip():
                    kind = "unsupported_scope"
                    scope = "unknown"
                    reason = "Model nie wskazał sposobu empirycznej weryfikacji."
            except AnalysisError:
                # Do not silently turn an unreviewed item into a verified
                # factual assertion. Its uncertainty is visible in the UI.
                if kind not in ("opinion", "verifiable"):
                    kind, scope = "unsupported_scope", "unknown"
                reason = "Nie udało się rozstrzygnąć rodzaju wypowiedzi."
            claims.append(
                Claim(
                    f"c{len(claims) + 1}", wording, kind, scope,
                    empirical_test, reason, reviewed,
                )
            )
        if not claims:
            return _nonclaim(text if not _PROMPT_INSTRUCTIONS.search(text) else "")
        query = result.query_text.strip()
        if (
            not query or query.casefold() not in text.casefold()
            or _PROMPT_INSTRUCTIONS.search(query)
        ):
            query = next((c.text for c in claims if c.kind == "verifiable"), "")
        after = _explicit_date(result.date_from, text, time.min)
        before = _explicit_date(result.date_to, text, time.max)
        if after and before and after > before:
            after, before = None, None
        entities = tuple(
            dict.fromkeys(name.strip() for name in result.entities if _grounded(name, text))
        )
        location = result.location.strip() if _grounded(result.location, text) else None
        return EventQuery(query, entities, location, after, before), tuple(claims)

    async def verify(self, claim: Claim, evidence: Sequence[EvidencePassage]) -> Verification:
        if claim.kind == "opinion":
            return Verification(Verdict.NOT_VERIFIABLE, "Opinia lub ocena wartościująca; brak obiektywnego kryterium prawdziwości.")
        if claim.kind != "verifiable":
            return Verification(Verdict.NOT_VERIFIABLE, "Nie rozpoznano samodzielnego twierdzenia lub klasyfikacja pozostaje niepewna.")
        if claim.scope == "private":
            return Verification(
                Verdict.INSUFFICIENT_EVIDENCE,
                "Twierdzenie dotyczy konkretnej osoby lub prywatnej sytuacji; "
                "potrzebne są dane dotyczące tego przypadku. Ogólna wiedza nie wystarcza.",
            )
        if self.public_sources_only:
            evidence = [passage for passage in evidence if eligible_model_url(passage.url)]
        supplied = {passage.passage_id: passage for passage in evidence[:PASSAGE_LIMIT]}
        if not supplied:
            return Verification(Verdict.INSUFFICIENT_EVIDENCE, "Brak pasujących fragmentów źródeł.")
        result = await self._complete(
            _Judgment,
            {
                "task": "verify",
                "claim": claim.text,
                "passages": [
                    {
                        "passage_id": passage.passage_id,
                        "text": passage.exact_text[:PASSAGE_CHARACTERS],
                        "published_at": passage.published_at.isoformat()
                        if passage.published_at
                        else None,
                    }
                    for passage in supplied.values()
                ],
            },
        )
        citations: dict[tuple[str, str, EvidenceRelation], EvidenceCitation] = {}
        for candidate in result.citations:
            quote = candidate.quote
            passage = supplied.get(candidate.passage_id)
            if (
                passage is not None
                and any(character.isalnum() for character in quote)
                and quote in passage.exact_text[:PASSAGE_CHARACTERS]
                and not _PROMPT_INSTRUCTIONS.search(passage.exact_text)
                and (
                    candidate.relation is EvidenceRelation.CONTEXT
                    or _has_claim_anchors(claim.text, quote)
                )
            ):
                key = (candidate.passage_id, quote, candidate.relation)
                citations[key] = EvidenceCitation(passage, quote, candidate.relation)
        return _evaluate(tuple(citations.values()), result.missing_context)

    async def _complete(self, schema: type[Output], payload: dict) -> Output:
        raw = await self.model.complete_json(
            schema=schema.model_json_schema(), prompt=json.dumps(payload, ensure_ascii=False)
        )
        try:
            return schema.model_validate(raw)
        except ValidationError:
            raise AnalysisError(
                ErrorCode.MODEL_UNAVAILABLE, "Model returned an invalid result."
            ) from None


def _has_claim_anchors(claim: str, quote: str) -> bool:
    """Reject clearly mismatched verbatim excerpts before accepting a model label."""
    terms = {
        word
        for word in _ANCHOR_WORDS.findall(claim.casefold())
        if len(word) > 2 and word not in _CONNECTORS and not word.isdecimal()
    }
    quote_terms = set(_ANCHOR_WORDS.findall(quote.casefold()))
    return bool(terms) and len(terms & quote_terms) >= min(2, len(terms))


def _nonclaim(text: str) -> tuple[EventQuery, tuple[Claim, ...]]:
    return EventQuery(""), (Claim("c1", text[:_CLAIM_CHARACTERS] or "Brak tezy.", "non_claim"),)


def _explicit_date(value: date | None, text: str, boundary: time) -> datetime | None:
    if value and value.isoformat() in text:
        return datetime.combine(value, boundary, UTC)
    return None


def _grounded(value: str | None, text: str) -> bool:
    return bool(
        value
        and value.strip()
        and value.strip().casefold() in text.casefold()
        and not _PROMPT_INSTRUCTIONS.search(value)
    )


def _evaluate(citations: tuple[EvidenceCitation, ...], missing_context: bool) -> Verification:
    relations = {citation.relation for citation in citations}
    supports = EvidenceRelation.SUPPORTS in relations
    refutes = EvidenceRelation.REFUTES in relations
    if supports and refutes:
        return Verification(Verdict.INSUFFICIENT_EVIDENCE, "Źródła są sprzeczne.", citations)
    if refutes:
        return Verification(Verdict.FALSE, "Zacytowany fragment przeczy tezie.", citations)
    if supports and missing_context:
        support_spans = {
            (citation.passage.passage_id, citation.quote)
            for citation in citations
            if citation.relation is EvidenceRelation.SUPPORTS
        }
        if any(
            citation.relation is EvidenceRelation.CONTEXT
            and (citation.passage.passage_id, citation.quote) not in support_spans
            for citation in citations
        ):
            return Verification(
                Verdict.MISLEADING, "Teza pomija istotny kontekst wskazany w źródle.", citations
            )
        return Verification(
            Verdict.INSUFFICIENT_EVIDENCE, "Brak potwierdzenia w pełnym kontekście.", citations
        )
    if supports:
        return Verification(Verdict.CONFIRMED, "Zacytowany fragment popiera tezę.", citations)
    return Verification(
        Verdict.INSUFFICIENT_EVIDENCE,
        "Brak weryfikowalnego cytatu rozstrzygającego tezę.",
        citations,
    )
