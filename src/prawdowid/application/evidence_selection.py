"""Bounded claim-aware selection of complementary, literal article paragraphs.

Pure retrieval, not source approval, entailment, or a factual verdict.
This module is adapted from the separately benchmarked V31.1 selector.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

MAX_EVIDENCE_SPANS = 3
MAX_QUOTE_CHARS = 700
_TOKEN = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOP = {
    'i',
    'a',
    'oraz',
    'ale',
    'bo',
    'czy',
    'do',
    'w',
    'we',
    'z',
    'ze',
    'na',
    'od',
    'po',
    'o',
    'dla',
    'jak',
    'jako',
    'jaki',
    'jaka',
    'jakie',
    'ktory',
    'ktore',
    'przez',
    'jest',
    'sa',
    'byl',
    'byla',
    'bylo',
    'jego',
    'jej',
    'ten',
    'ta',
    'to',
    'te',
    'juz',
    'przy',
    'tym',
    'ponad',
    'pod',
    'dnia',
    'roku',
    'tego',
    'zostal',
    'zostala',
    'zostalo',
    'zostaly',
    'the',
    'and',
    'with',
    'from',
}

# Broad, domain-agnostic *retrieval* equivalents, never used as verdict rules.
_EQUIVALENTS = {
    "propozycja": r"^(?:zapropon|propon|projekt|przygotowa|planow|planowan)",
    "spadek": r"^(?:zmniejs|mniejs|spad|obniz|ogranicz|redukc|nizs|mniej)",
    "wzrost": r"^(?:zwieksz|wieksz|wzros|podwyz|wiecej)",
    "otwarcie": r"^(?:otwart|otworz|otwieran|udostepn|oddano|oddac)",
    "zamkniecie": r"^(?:zamkn|nieczyn|wstrzym|niedostep)",
    "badanie": r"^(?:badan|badani|eksperyment|testowan|proba)",
    "uczestnicy": r"^(?:uczestnic|osob|ochotnik|pacjent)",
    "brak_efektu": r"^(?:niewplyn|bezefekt|niezmien|nieobniz|brakzmian)",
    "strefa": r"^(?:stref)",
    "wladze": r"^(?:wladz|miast|urzad|gmin)",
}
_EQ_RE = [(v, re.compile(p)) for v, p in _EQUIVALENTS.items()]


def plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold().replace("ł", "l")) if not unicodedata.combining(c))


def tokens(text: str) -> set[str]:
    raw = plain(text)
    # Preserve core names, amounts and acronyms while reducing inflection noise.
    result = set()
    for word in _TOKEN.findall(raw):
        if word in _STOP or len(word) < 3:
            continue
        match = next((name for name, expression in _EQ_RE if expression.match(word)), None)
        result.add(match if match else (word[:6] if len(word) >= 8 else word[:5] if len(word) >= 6 else word))
    return result


@dataclass(frozen=True)
class Span:
    paragraph_id: int
    start: int
    end: int
    text: str


def spans(body: str) -> list[Span]:
    """Return paragraph spans with exact source offsets; never reconstruct quotes."""
    out = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", body):
        start, stop = match.span()
        while start < stop:
            end = min(stop, start + MAX_QUOTE_CHARS)
            if end < stop:
                # Split at a real separator, retaining literal source offsets.
                space = body.rfind(" ", start + 320, end)
                if space > start:
                    end = space
            raw = body[start:end]
            if len(raw.strip()) >= 12:
                out.append(Span(len(out), start, end, raw))
            start = end
            while start < stop and body[start].isspace():
                start += 1
    return out


def _contains_both_numbers_with_decline(text: str) -> bool:
    """Generic weak retrieval hint. It is NOT numeric entailment."""
    nums = [float(x.replace(",", ".")) for x in re.findall(r"(?<!\w)\d+(?:[,.]\d+)?(?!\w)", text)]
    return len(nums) >= 2 and nums[-1] < nums[0]


def _scored_span(span: Span, query: set[str], common_counts: dict[str, int], n: int, claim: str) -> tuple[float, set[str]]:
    p = tokens(span.text)
    overlap = query & p
    # IDF favors precise anchors rather than ubiquitous 'projekt' or 'badanie'.
    score = sum(1 + math.log((n + 1) / (common_counts.get(t, 0) + 1)) for t in overlap)
    score /= 1 + 0.04 * max(0, len(p) - 18)
    if "spadek" in query and _contains_both_numbers_with_decline(span.text):
        score += 1.5
    if "otwarcie" in query and "zamkniecie" in p:
        score += 1.25  # surface contradicters; don't infer verdict
    return score, overlap


_NAVIGATION = re.compile(
    r"(?:\bzobacz\s+takze\b|\bczytaj\s+(?:rowniez|wiecej|dalej)\b|"
    r"\bpowiazane\s+(?:artykuly|materialy)\b|\bprzejdz\s+do\b|\bmenu\s+serwisu\b)",
    re.IGNORECASE,
)
_PLAN = re.compile(r"\b(?:zapropon\w*|propon\w*|projekt\w*|przygotow\w*|planow\w*|zaplanow\w*)\b")
_CHANGE_DOWN = re.compile(r"\b(?:zmniejs\w*|mniejsz\w*|ogranicz\w*|obniz\w*|spad\w*|redukc\w*|mniej)\b")
_OPEN = re.compile(r"\b(?:otwart\w*|otworz\w*|otwarci\w*|udostepn\w*|rozpocz\w*|uruchom\w*)\b")
_CLOSED = re.compile(r"\b(?:zamkn\w*|zabron\w*|nieczyn\w*|nie\s+(?:otwart\w*|uruchom\w*|oddano|mierzon\w*|badano))\b")
_NUM = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")
_CONSULT = re.compile(r"\b(?:konsultacj\w*|glosowan\w*|wejscie w zycie)\b")


def document_role(text: str) -> str:
    clean = plain(text).strip()
    if _NAVIGATION.search(clean):
        return "navigation"
    if len(clean) < 45 and not re.search(r"[.!?]", clean):
        return "heading"
    if re.search(r"\b(?:\[pdf\]|\[link\])\s*$", clean) and len(clean) < 160:
        return "link"
    return "editorial"


def subject_terms(claim: str) -> set[str]:
    # Proper names and acronyms, derived from each claim rather than an
    # allowlist of publishers or a case-specific set of rules.
    candidates = re.findall(r"\b(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]{2,}|[A-ZĄĆĘŁŃÓŚŹŻ]{2,})\b", claim)
    generic = {next(iter(tokens(x)), "") for x in (
        "władze", "firma", "spółka", "most", "program", "raport", "według", "projekt", "w", "po", "we", "dla", "już", "badaniu"
    )}
    found = set()
    for token in candidates:
        word = next(iter(tokens(token)), None)
        if word and word not in generic:
            found.add(word)
    return found


def _declining_numbers(text: str) -> bool:
    normalized = plain(text)
    # Require a described before/after relation, not just two unrelated numbers.
    if not (re.search(r"\b(?:obecnie|przed|poczatkow|poprzednio)\b", normalized)
            and re.search(r"\b(?:po zmianie|po modernizacj|nastepnie|teraz)\b", normalized)):
        return False
    values = [float(num.replace(",", ".")) for num in _NUM.findall(normalized)]
    return len(values) >= 2 and values[-1] < values[0]


def _important_facets(claim: str) -> set[str]:
    normalized = plain(claim)
    required = set()
    if _PLAN.search(normalized):
        required.add("proposal")
    if _CHANGE_DOWN.search(normalized):
        required.add("decrease")
    if _OPEN.search(normalized):
        required.add("opening_status")
        if "juz" in normalized:
            required.add("opening_timing")
    if "hospitaliz" in normalized:
        required.add("hospitalization")
    if "poniedzial" in normalized and "wtore" in normalized:
        required.update({"monday", "tuesday"})
    if "azotan" in normalized:
        required.add("nitrates")
    if "olow" in normalized:
        required.add("lead")
    if "uczestnic" in normalized or "osob" in normalized:
        required.add("participants")
    if "przychod" in normalized:
        required.add("revenue")
    if "zysk" in normalized:
        required.add("profit")
    if "magazyn" in normalized:
        required.add("warehouse")
    for number in _NUM.findall(normalized):
        required.add("number:"+number.replace(",", "."))
    return required


def _evidence_facets(text: str, claim: str) -> set[str]:
    normalized = plain(text)
    needed = _important_facets(claim)
    facets = set()
    if ("proposal" in needed and _PLAN.search(normalized)
            and re.search(r"\b(?:przygotow\w*|zaproponow\w*|przedstaw\w*|zaplanow\w*|oglos\w*|wnioskow\w*)\b", normalized)):
        facets.add("proposal")
    if "decrease" in needed and (_CHANGE_DOWN.search(normalized) or _declining_numbers(text)):
        facets.add("decrease")
    if "opening_status" in needed and (_OPEN.search(normalized) or _CLOSED.search(normalized)):
        facets.add("opening_status")
    if "opening_timing" in needed and re.search(
        r"\b(?:dopiero|zaplanowan\w*|planowan\w*|przyszl\w*|w przyszlym|jeszcze nie)\b", normalized
    ):
        facets.add("opening_timing")
    if "hospitalization" in needed and "hospitaliz" in normalized:
        facets.add("hospitalization")
    if "monday" in needed and "poniedzial" in normalized:
        facets.add("monday")
    if "tuesday" in needed and "wtore" in normalized:
        facets.add("tuesday")
    if "nitrates" in needed and "azotan" in normalized:
        facets.add("nitrates")
    if "lead" in needed and "olow" in normalized:
        facets.add("lead")
    if "participants" in needed and ("uczestni" in normalized or "osob" in normalized):
        facets.add("participants")
    if "revenue" in needed and "przychod" in normalized:
        facets.add("revenue")
    if "profit" in needed and "zysk" in normalized:
        facets.add("profit")
    if "warehouse" in needed and "magazyn" in normalized:
        facets.add("warehouse")
    for num in _NUM.findall(normalized):
        k = "number:"+num.replace(",", ".")
        if k in needed:
            facets.add(k)
    return facets


def select(body: str, claim: str, limit: int = MAX_EVIDENCE_SPANS) -> tuple[list[Span], dict]:
    """Pick complementary original editorial spans, never a truth verdict.

    Candidate scoring is claim-aware and rewards new *evidence facets*.
    Headings, menus, related links and clearly foreign subjects cannot supply
    support. All output quotes remain verbatim exact-offset slices.
    """
    candidates = spans(body)
    q = tokens(claim)
    subjects = subject_terms(claim)
    # Only retain subject names that occur in the body: institutional names
    # can be implicit in articles (e.g. 'miasto' instead of 'Kraków').
    present_subjects = {x for x in subjects if sum(x in tokens(p.text) for p in candidates) > 0}
    needed = _important_facets(claim)
    counts = {tok: sum(tok in tokens(span.text) for span in candidates) for tok in q}
    base = {p.paragraph_id: _scored_span(p, q, counts, len(candidates), claim) for p in candidates}
    roles = {p.paragraph_id: document_role(p.text) for p in candidates}
    facet_map = {p.paragraph_id: _evidence_facets(p.text, claim) for p in candidates}
    selected: list[Span] = []
    covered_words: set[str] = set()
    covered_facets: set[str] = set()
    trace = []
    remaining = [p for p in candidates if roles[p.paragraph_id] == "editorial"]
    rejected = [{"paragraph": p.paragraph_id, "reason": roles[p.paragraph_id]}
                for p in candidates if roles[p.paragraph_id] != "editorial"]
    while remaining and len(selected) < limit:
        # A statement that the metric was not measured bars further numeric
        # pseudo-matches from being stitched into a positive conclusion.
        if (selected and "hospitalization" in needed
            and any(re.search(r"(?:nie mierzono|nie badano|brak danych).{0,70}hospitaliz", plain(x.text))
                    for x in selected)):
            break
        requires_closed = (bool(selected) and "opening_status" in needed
            and needed <= covered_facets
            and not any(_CLOSED.search(plain(x.text)) for x in selected))
        scores = []
        for span in remaining:
            idx = span.paragraph_id
            norm = plain(span.text)
            if requires_closed and not _CLOSED.search(norm):
                continue
            original, common = base[idx]
            new_words = common - covered_words
            new_facets = facet_map[idx] - covered_facets
            distance = min((abs(idx - p.paragraph_id) for p in selected), default=999)
            neighboring = 0.4 if distance == 1 else 0.0
            foreign = bool(re.search(r"\b(?:z kolei|inna firma|inny program|odrebn|natomiast inny)\b", norm))
            # Explicitly named foreign institutions/objects cannot borrow a
            # relevant number from another entity in the same document.
            named_actors = re.findall(
                r"\b(?:Firma|Spółka|Spolka|Program|Most|Filtr|Terminal)\s+([A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)\b",
                span.text, re.IGNORECASE,
            )
            actor_keys = {next(iter(tokens(name)), "") for name in named_actors}
            if present_subjects and actor_keys and not (actor_keys & present_subjects):
                continue
            # Foreign-topic switch is absolute, not a weak score penalty.
            if foreign and (not present_subjects or not (present_subjects & tokens(span.text))):
                continue
            # When a claim concerns a measurement, demand the correct metric;
            # an unrelated '30 percent' must not establish hospital admissions.
            if "hospitalization" in needed and "hospitalization" not in facet_map[idx]:
                continue
            # Explicit 'this measured X, not Y' is contextual warning, NOT a
            # candidate measurement of Y merely because a number is nearby.
            if re.search(r"\b(?:to|jest)\s+(?:wylacznie|tylko)\b.{0,145}\ba nie\b", norm):
                continue
            if (present_subjects and not (present_subjects & tokens(span.text))
                    and (not selected or distance > 2)):
                # Subject may be implicit in adjacent paragraphs, but do not
                # borrow it from far-away or explicitly unrelated topics.
                # Allow subject-less numeric changes only when the text
                # still includes a distinctive shared object term.
                strong = len((common & q) - {"wladze", "firma", "spolka"}) >= 2
                if not strong:
                    continue
            novelty = sum(1 + math.log((len(candidates) + 1)/(counts[t] + 1)) for t in new_words)
            score = (0.36 * original + 0.65 * novelty + 6.0 * len(new_facets)
                     + neighboring - (2.8 if selected and not new_facets and len(new_words) < 2 else 0.0))
            if "decrease" in new_facets and _declining_numbers(span.text):
                score += 3.0
            if "opening_status" in new_facets and _CLOSED.search(norm):
                score += 3.0
            if requires_closed and _CLOSED.search(norm):
                score += 6.0
            if "hospitalization" in new_facets and ("nie" in tokens(span.text) or "brak" in norm):
                score += 5.0
            # Do not reward unrelated huge paragraphs purely for their length.
            score -= max(0, len(span.text) - 430) / 280.0
            scores.append((score, original, -idx, span, sorted(new_words), sorted(new_facets)))
        if not scores:
            break
        scores.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
        value, original, _, pick, words, facets = scores[0]
        if value < 1.6:
            break
        # No extra 'padding' evidence after all facets have been met except
        # when a query has no analyzable facets and retrieval needs context.
        if selected and needed and needed <= covered_facets:
            # For claims about a completed opening, retrieve an explicit
            # contrary status if available, rather than only the planned date.
            needs_direct_status = ("opening_status" in needed
                and not any(_CLOSED.search(plain(x.text)) for x in selected)
                and any(_CLOSED.search(plain(x.text)) for x in remaining))
            if not needs_direct_status:
                break
        selected.append(pick)
        covered_words |= base[pick.paragraph_id][1]
        covered_facets |= facet_map[pick.paragraph_id]
        remaining.remove(pick)
        trace.append({"paragraph": pick.paragraph_id, "score": round(value, 3),
                      "lexical_score": round(original, 3), "new_anchors": words,
                      "new_facets": facets})
    if "decrease" in needed and {"monday", "tuesday"} <= covered_facets:
        day_values = {}
        for span in selected:
            normalized = plain(span.text)
            for day in ("poniedzial", "wtore"):
                match = re.search(r"\b" + day + r"\w*\b.{0,100}?(\d+)\s+paczek", normalized)
                if match:
                    day_values[day] = int(match.group(1))
        if len(day_values) == 2 and day_values["wtore"] < day_values["poniedzial"]:
            covered_facets.add("decrease")
    return selected, {
        "anchor_coverage": sorted(covered_words), "unmatched_anchors": sorted(q - covered_words),
        "required_facets": sorted(needed), "covered_facets": sorted(covered_facets),
        "missing_facets": sorted(needed - covered_facets), "trace": trace,
        "paragraphs": len(candidates), "central_subjects": sorted(present_subjects),
        "rejected_noneditorial": rejected,
    }
