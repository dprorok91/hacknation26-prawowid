"""Exact text segmentation and a conservative privacy guard, not factuality rules."""

import re

_PERSONAL = re.compile(
    r"\b(?:mój|moja|moje|mojego|mojej|moim|moją|moi|moich|moimi|"
    r"mam|posiadam|u mnie|mój\s+sąsiad)\b",
    re.IGNORECASE,
)


def private_reference(text: str) -> bool:
    """Extra do-not-send safeguard for personal statements; no verdict inference."""
    return bool(_PERSONAL.search(text))


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """Find exact substring spans; punctuation is only a possible segment boundary."""
    spans = []
    start = 0
    for match in re.finditer(r"[.!?](?=\s|$)", text):
        raw = text[start : match.end()]
        stripped = raw.strip()
        if stripped:
            offset = start + len(raw) - len(raw.lstrip())
            spans.append((stripped, offset, offset + len(stripped)))
        start = match.end()
    trailing = text[start:]
    if trailing.strip():
        stripped = trailing.strip()
        offset = start + len(trailing) - len(trailing.lstrip())
        spans.append((stripped, offset, offset + len(stripped)))
    return spans[:5]


def candidate_spans(text: str) -> list[str]:
    """Bounded exact sentence/list spans for under-extraction detection.

    These are *candidates*, never assumed to be true or factual. Each candidate
    must independently pass semantic classification before public retrieval.
    """
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return list(dict.fromkeys(
        chunk.strip() for chunk in chunks
        if 12 <= len(chunk.strip()) <= 400
    ))[:80]
