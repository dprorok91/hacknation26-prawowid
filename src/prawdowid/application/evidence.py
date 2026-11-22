"""Extract claims and apply conservative verdicts to grounded model judgments."""

import json
import logging
import re
from calendar import monthrange
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.application.claims import candidate_spans, sentence_spans
from prawdowid.application.evidence_selection import _evidence_facets, _important_facets, tokens
from prawdowid.domain.models import Claim, EventQuery, EvidencePassage, EvidenceRelation, Verdict
from prawdowid.domain.ports import StructuredLLM
from prawdowid.domain.semantics import relevance_form

Output = TypeVar("Output", bound=BaseModel)
logger = logging.getLogger(__name__)

PASSAGE_LIMIT = 10
PASSAGE_CHARACTERS = 2000
INPUT_CHARACTERS = 18000
_CLAIM_CHARACTERS = 400
_ANCHOR_WORDS = re.compile(r"[^\W_]+")
_CONNECTORS = {"jest", "była", "było", "które", "który", "oraz", "według", "także", "tego", "roku"}
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

        # A model returning just one claim from an article must not silently
        # erase the rest of its independent assertions. Use exact source spans
        # as *candidates*, never as factual judgments or evidence.
        candidates = [
            sentence for sentence in candidate_spans(text)
            if not _PROMPT_INSTRUCTIONS.search(sentence)
        ]
        remaining = [
            sentence for sentence in candidates
            if not any(sentence in claim or claim in sentence for claim in selected)
        ]
        slots = max(0, 5 - len(selected))
        if slots and remaining:
            if len(remaining) > slots > 1:
                indices = [round(index * (len(remaining) - 1) / (slots - 1))
                           for index in range(slots)]
                remaining = [remaining[index] for index in indices]
            elif len(remaining) > slots:
                remaining = [remaining[len(remaining) // 2]]
            for sentence in remaining[:slots]:
                selected[sentence] = _Proposition(
                    text=sentence, kind="unsupported_scope"
                )
        if not selected:
            for sentence, _, _ in sentence_spans(text):
                if len(sentence) <= _CLAIM_CHARACTERS and not _PROMPT_INSTRUCTIONS.search(sentence):
                    selected[sentence] = _Proposition(
                        text=sentence, kind="unsupported_scope"
                    )

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
        supplied = {passage.passage_id: passage for passage in evidence[:PASSAGE_LIMIT]}
        if not supplied:
            return Verification(Verdict.INSUFFICIENT_EVIDENCE, "Brak pasujących fragmentów źródeł.")
        payload = {
            "task": "verify",
            "claim": claim.text,
            "passages": [
                {
                    "passage_id": passage.passage_id,
                    "text": passage.exact_text[:PASSAGE_CHARACTERS],
                    "published_at": passage.published_at.isoformat()
                    if passage.published_at
                    else None,
                    "calendar_check": _calendar_check(claim.text, passage.exact_text),
                }
                for passage in supplied.values()
            ],
        }
        # Source-only verbatim options help the model cite a complete proposal
        # (action + scope) rather than just one of its adjacent paragraphs.
        # They are NOT verdicts and are never promoted to citations directly.
        options = _focused_live_quotes(claim.text, tuple(supplied.values()))
        if options:
            payload["exact_quote_options"] = options
        # These are retrieval group identifiers, not an assertion of truth.
        multi_groups = _multispan_groups(claim.text, tuple(supplied.values()))
        if multi_groups:
            payload["complementary_passage_groups"] = [
                {"passage_ids": [p.passage_id for p in passages],
                 "same_document": True,
                 "instruction": "Cite each necessary literal passage separately; do not combine quotes."}
                for passages in multi_groups.values()
            ]
        result = await self._complete(_Judgment, payload)
        citations: dict[tuple[str, str, EvidenceRelation], EvidenceCitation] = {}
        reasons: Counter[str] = Counter()
        for attempt in range(2 if options else 1):
            citations = {}
            reasons = Counter()
            valid_quote_candidates: list[EvidenceCitation] = []
            for candidate in result.citations:
                quote = candidate.quote
                passage = supplied.get(candidate.passage_id)
                if passage is None:
                    reasons["unknown_passage"] += 1
                elif not any(character.isalnum() for character in quote):
                    reasons["empty_quote"] += 1
                elif quote not in passage.exact_text[:PASSAGE_CHARACTERS]:
                    reasons["not_verbatim"] += 1
                elif _PROMPT_INSTRUCTIONS.search(passage.exact_text):
                    reasons["unsafe_source_text"] += 1
                elif (
                    (calendar := _calendar_check(claim.text, quote)) is not None
                    and (
                        (candidate.relation is EvidenceRelation.SUPPORTS and calendar["conflicts"])
                        or (candidate.relation is EvidenceRelation.REFUTES and not calendar["conflicts"])
                    )
                ):
                    reasons["calendar_relation_mismatch"] += 1
                else:
                    valid_quote_candidates.append(EvidenceCitation(
                        passage, quote, candidate.relation
                    ))
            # Validate individual quotes as before. The only new case is a
            # *complete group* of literal quotes from the same reviewed source.
            approved_group_keys = _approved_multispan_citation_keys(
                claim.text, valid_quote_candidates, multi_groups
            )
            for item in valid_quote_candidates:
                key = (item.passage.passage_id, item.quote, item.relation)
                if key in approved_group_keys:
                    citations[key] = item
                elif (
                    item.relation is EvidenceRelation.SUPPORTS
                    and any(item.passage.passage_id in {
                        passage.passage_id for passage in group
                    } for group in multi_groups.values())
                    and not _important_facets(claim.text) <= _evidence_facets(
                        item.quote, claim.text
                    )
                ):
                    reasons["incomplete_compound_support"] += 1
                elif (
                    item.relation is EvidenceRelation.SUPPORTS
                    and not _number_anchors(claim.text) <= _number_anchors(item.quote)
                ):
                    reasons["numerical_support_mismatch"] += 1
                elif (
                    item.relation is not EvidenceRelation.CONTEXT
                    and not _has_claim_anchors(claim.text, item.quote)
                ):
                    reasons["claim_anchor_mismatch"] += 1
                else:
                    citations[key] = item
            if citations or not options or attempt == 1:
                break
            try:
                result = await self._complete(
                    _Judgment,
                    {
                        **payload,
                        "verification_retry": (
                            "The previous citations were empty or invalid. "
                            "Evaluate complementary_passage_groups as joint context, "
                            "and quote each necessary passage literally with its ID. "
                            "Do not claim that a proposal is already in force. "
                            "Abstain if any necessary part lacks evidence."
                        ),
                    },
                )
            except AnalysisError:
                break
        if not citations:
            logger.warning(
                "Evidence verification produced no validated citations: "
                "passages=%d model_citations=%d reasons=%s live_options=%d",
                len(supplied), len(result.citations),
                dict(sorted(reasons.items())), len(options),
            )
        return _evaluate(tuple(citations.values()), result.missing_context)

    async def _complete(self, schema: type[Output], payload: dict) -> Output:
        # The provider's strict schema strips some array-size constraints.
        # Bound oversized extraction arrays locally before Pydantic validation;
        # the pipeline reports that analyses of long articles are partial.
        for attempt in range(2):
            request = payload if attempt == 0 else {
                **payload,
                "schema_retry": (
                    "The previous JSON did not satisfy the schema. "
                    "Return valid fields and values only; at most five claims."
                ),
            }
            raw = await self.model.complete_json(
                schema=schema.model_json_schema(),
                prompt=json.dumps(request, ensure_ascii=False),
            )
            if schema is _Extraction and isinstance(raw, dict):
                items = raw.get("claims")
                if isinstance(items, list) and len(items) > 5:
                    logger.warning("Extraction exceeded five claims; bounding the response")
                    raw = {**raw, "claims": items[:5]}
            try:
                return schema.model_validate(raw)
            except ValidationError as exc:
                # Only error types are logged. Never log untrusted fields, prompt,
                # source text, Pydantic inputs or model-generated strings.
                kinds = sorted({issue["type"] for issue in exc.errors(include_input=False)})
                logger.warning(
                    "Structured model output failed schema: %s types=%s attempt=%d",
                    schema.__name__, kinds[:5], attempt + 1,
                )
        raise AnalysisError(
            ErrorCode.MODEL_UNAVAILABLE, "Model returned an invalid result."
        )


_DATE_DOTTED = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4,
    "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
    "września": 9, "wrzesnia": 9, "października": 10, "pazdziernika": 10,
    "listopada": 11, "grudnia": 12,
}
_DATE_POLISH = re.compile(
    r"(?<!\d)(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})(?!\d)",
    re.IGNORECASE,
)


def _claim_anchor_terms(text: str) -> set[str]:
    """Conservative lexical anchors with common date and acronym equivalents.

    This does not infer factual support. The model must still return a literal
    quote and its passage ID, and the citation must pass exact-quote checks.
    """
    lower = text.casefold()
    terms = {
        relevance_form(word) for word in _ANCHOR_WORDS.findall(lower)
        if len(word) > 2 and word not in _CONNECTORS and not word.isdecimal()
    }
    if "strefa czystego transportu" in lower:
        terms.add("sct")
    for match in _DATE_DOTTED.finditer(lower):
        day, month, year = map(int, match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            terms.add(f"date:{year:04d}-{month:02d}-{day:02d}")
    for match in _DATE_ISO.finditer(lower):
        year, month, day = map(int, match.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            terms.add(f"date:{year:04d}-{month:02d}-{day:02d}")
    for match in _DATE_POLISH.finditer(lower):
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name)
        if month is not None and 1 <= int(day) <= 31:
            terms.add(f"date:{int(year):04d}-{month:02d}-{int(day):02d}")
    return terms


def _number_anchors(text: str) -> set[str]:
    """Require literal numeric support; dates are compared across common formats."""
    dates = {term for term in _claim_anchor_terms(text) if term.startswith("date:")}
    # Years or quantities not already included in a normalized complete date.
    without_dates = _DATE_DOTTED.sub(" ", _DATE_ISO.sub(" ", _DATE_POLISH.sub(" ", text)))
    numbers = set(re.findall(r"(?<!\w)\d+(?!\w)", without_dates))
    return dates | numbers


def _has_claim_anchors(claim: str, quote: str) -> bool:
    """Reject topic-only quotes that do not discuss the proposition itself.

    A minimum of two shared words alone is unsafe for long causal or
    outcome claims: even a quote about an unrelated date can share those.
    This is a conservative relevance guard, not a truth classifier.
    """
    all_terms = _claim_anchor_terms(claim)
    all_quoted = _claim_anchor_terms(quote)
    # Exact dates plus an explicitly curated subject alias retain precision
    # even if the source abbreviates the subject and inflects the verb.
    common_dates = {
        term for term in all_terms & all_quoted if term.startswith("date:")
    }
    if common_dates and "sct" in all_terms & all_quoted:
        return True
    # Conservative event-specific paraphrase: "władze zaproponowały
    # zmniejszenie SCT" and "miasto przygotowało projekt, mniejsza strefa".
    # Both the proposal AND smaller-zone predicates must be present in the
    # literal quote. A quote about an enacted rule cannot satisfy this.
    if (
        "sct" in all_terms & all_quoted
        and re.search(r"zapropon|propon|projekt", claim.casefold())
        and re.search(r"zmniejsz|mniejsz|ogranicz", claim.casefold())
        and re.search(r"propon|projekt|przygotowa", quote.casefold())
        and re.search(r"zmniejsz|mniejsz|ogranicz", quote.casefold())
    ):
        return True
    terms = {word for word in all_terms if not word.startswith("date:")}
    quoted = {word for word in all_quoted if not word.startswith("date:")}
    if not terms:
        return False
    shared = terms & quoted
    return len(shared) >= min(2, len(terms)) and 2 * len(shared) >= len(terms)



def _multispan_groups(
    claim: str, passages: tuple[EvidencePassage, ...]
) -> dict[str, tuple[EvidencePassage, ...]]:
    """Only group excerpts explicitly selected for this claim from ONE live page."""
    groups: dict[tuple[str, str], list[EvidencePassage]] = {}
    for passage in passages:
        if (
            passage.passage_id.startswith("live:")
            and any(
                claim == label or claim.endswith(". " + label)
                for label in getattr(passage, "selected_for", ())
            )
            and urlsplit(passage.url).hostname == "zdmk.krakow.pl"
        ):
            groups.setdefault((passage.document_id, passage.url), []).append(passage)
    return {
        doc_id: tuple(items[:3])
        for (doc_id, _url), items in groups.items() if len(items) >= 2
    }


def _enacted_claim(claim: str) -> bool:
    lower = claim.casefold()
    return bool(re.search(
        r"\b(?:już\s+)?(?:wprowadz\w*|obowiąz\w*|uchwal\w*|weszł\w*\s+w\s+życie)\b",
        lower,
    ))


def _approved_multispan_citation_keys(
    claim: str,
    candidates: list[EvidenceCitation],
    groups: dict[str, tuple[EvidencePassage, ...]],
) -> set[tuple[str, str, EvidenceRelation]]:
    """Safely admit complementary *supporting* quotes, never inferred verdicts.

    Does NOT merge quotes or accept evidence from different documents. Even
    with facet coverage, the LLM has to cite each passage as 'supports'.
    Claims about enacted policy never acquire support from proposed changes.
    """
    if _enacted_claim(claim):
        return set()
    needed = _important_facets(claim)
    if len(needed) < 2:
        return set()
    approved = set()
    for passages in groups.values():
        ids = {p.passage_id for p in passages}
        same_source = [
            item for item in candidates
            if item.passage.passage_id in ids
            and item.relation is EvidenceRelation.SUPPORTS
        ]
        if len({item.passage.passage_id for item in same_source}) < 2:
            continue
        # Reuse selector facets but only from the precise model-cited quote.
        # An unrelated number from a different metric cannot fill a gap.
        covered: set[str] = set()
        for item in same_source:
            covered |= _evidence_facets(item.quote, claim)
        if not needed <= covered:
            continue
        # Required facets alone are too weak. At least two non-generic
        # semantic anchors must connect the claim with the cited excerpts.
        shared = tokens(claim) & tokens("\n\n".join(
            item.quote for item in same_source
        ))
        if len(shared) < 2:
            continue
        # Publication-date assertions require published_at, never a headline.
        if re.search(r"opublikow|publikacj", claim.casefold()) and not all(
            item.passage.published_at for item in same_source
        ):
            continue
        approved.update(
            (item.passage.passage_id, item.quote, item.relation)
            for item in same_source
        )
    return approved


def _focused_live_quotes(
    claim: str, passages: tuple[EvidencePassage, ...]
) -> list[dict[str, str]]:
    """Provide bounded literal quote options; NEVER assert that they are true.

    Only reviewed ephemeral municipal evidence gets this optional assistance.
    All options are exact contiguous substrings from a supplied passage; the
    regular evidence validator still checks verbatim text and claim anchors.
    """
    # A proposal cannot, by itself, prove an enacted change. Avoid retrying
    # a confirmation-focused citation request for such claims.
    if re.search(r"\b(?:już\s+)?(?:wprowadz\w*|obowiąz\w*|uchwal\w*)\b", claim.casefold()):
        return []
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for passage in passages:
        if (
            not passage.passage_id.startswith("live:")
            or urlsplit(passage.url).hostname != "zdmk.krakow.pl"
        ):
            continue
        body = passage.exact_text[:PASSAGE_CHARACTERS]
        if any(
            claim == label or claim.endswith(". " + label)
            for label in getattr(passage, "selected_for", ())
        ):
            # The benchmarked selector has already chosen a claim-specific
            # editorial span; this is an exact quote OPTION, never a verdict.
            if (20 <= len(body) <= 500 and not _PROMPT_INSTRUCTIONS.search(body)
                and body not in seen):
                seen.add(body)
                options.append({"passage_id": passage.passage_id, "quote": body})
            continue
        paragraphs = body.split("\n\n")
        windows = [body]
        for width in (1, 2, 3):
            for i in range(max(0, len(paragraphs) - width + 1)):
                windows.append("\n\n".join(paragraphs[i:i + width]))
        # Shortest complete, literal window first; the model may still abstain.
        for candidate in sorted(set(windows), key=len):
            if not (20 <= len(candidate) <= 500):
                continue
            if not _has_claim_anchors(claim, candidate):
                continue
            if _PROMPT_INSTRUCTIONS.search(candidate) or candidate in seen:
                continue
            seen.add(candidate)
            options.append({"passage_id": passage.passage_id, "quote": candidate})
            break
        if len(options) == 3:
            break
    return options


_CALENDAR_RULE = re.compile(
    r"\b(trzecią|trzecia|pierwszą|pierwsza|drugą|druga|czwartą|czwarta)\s+"
    r"(niedzielę|niedziela|poniedziałek|poniedziałku|sobotę|sobota)\s+"
    r"(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|"
    r"września|października|listopada|grudnia)\b",
    re.IGNORECASE,
)


def _calendar_check(claim: str, quote: str) -> dict[str, str | bool] | None:
    """Calendar arithmetic attached to evidence, never a fabricated source quote.

    Only applies to a celebration date (not publication, accident or policy
    dates), and a recurring ordinal weekday explicitly in the passage.
    The model must still check whether both statements refer to the same event.
    """
    lower = claim.casefold()
    if not re.search(r"\b(obchod\w*|przypad\w*)\b", lower):
        return None
    if re.search(r"\b(opublikow\w*|publikacj\w*)\b", lower):
        return None
    if not re.search(r"\b(?:co roku|każdego roku)\b", quote.casefold()):
        return None
    mentioned = _DATE_POLISH.search(lower)
    rule = _CALENDAR_RULE.search(quote)
    if mentioned is None or rule is None:
        return None
    day_string, month_name, year_string = mentioned.groups()
    day, year = int(day_string), int(year_string)
    month = _MONTHS.get(month_name)
    if month is None or month != _MONTHS.get(rule.group(3).casefold()):
        return None
    if year < 1900 or year > 2100:
        return None
    try:
        claimed = date(year, month, day)
    except ValueError:
        return None
    ordinal = {"pierwszą": 1, "pierwsza": 1, "drugą": 2, "druga": 2,
               "trzecią": 3, "trzecia": 3, "czwartą": 4, "czwarta": 4}[
        rule.group(1).casefold()
    ]
    weekday = {"poniedziałek": 0, "poniedziałku": 0, "sobotę": 5,
               "sobota": 5, "niedzielę": 6, "niedziela": 6}[rule.group(2).casefold()]
    first_weekday = date(year, month, 1).weekday()
    expected_day = 1 + (weekday - first_weekday) % 7 + 7 * (ordinal - 1)
    if expected_day > monthrange(year, month)[1]:
        return None
    expected = date(year, month, expected_day)
    return {
        "claimed_date": claimed.isoformat(),
        "rule_implied_date": expected.isoformat(),
        "conflicts": claimed != expected,
    }


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
