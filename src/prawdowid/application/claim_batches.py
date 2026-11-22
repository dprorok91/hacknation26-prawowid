"""Bounded segmentation of multi-statement articles for model extraction."""

import re

from prawdowid.application.claims import candidate_spans

_MAX_BATCHES = 2


def claim_batches(text: str) -> tuple[str, ...]:
    """Keep exact source text and prefer an editorial paragraph/sentence boundary."""
    # Two separate extractions are needed even for HTML articles whose bullets
    # have no punctuation. Short inputs remain a single request.
    if len(candidate_spans(text)) < 4 or len(text) < 240:
        return (text,)
    boundaries = [match.end() for match in re.finditer(r"[.!?](?=\s|$)|\n+", text)]
    if not boundaries:
        return (text,)
    middle = len(text) // 2
    split = min(boundaries, key=lambda item: abs(item - middle))
    if min(split, len(text) - split) < 100:
        return (text,)
    return (text[:split].strip(), text[split:].strip())[:_MAX_BATCHES]
