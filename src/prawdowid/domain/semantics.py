"""Curated Polish aliases for retrieval only; not evidence of event identity."""

import re

# Only curated, contextual abbreviations. Expansion is for retrieval, not an
# assertion that two news events (e.g. an announcement and a law) are the same.
_SCT = re.compile(r"(?<!\w)SCT(?!\w)", re.IGNORECASE)
_FULL_SCT = re.compile(r"\bstref(?:a|y|ie|ę|ą) czystego transportu\b", re.IGNORECASE)


def aliases_for(text: str) -> tuple[str, ...]:
    """Expose recognized concept equivalents, not semantic verdicts."""
    if _SCT.search(text) or _FULL_SCT.search(text):
        return ("SCT", "strefa czystego transportu")
    return ()


def expand_query(text: str) -> str:
    """Append a matching alias for retrieval without rewriting the claim."""
    if _SCT.search(text) and not _FULL_SCT.search(text):
        return f"{text} strefa czystego transportu"
    if _FULL_SCT.search(text) and not _SCT.search(text):
        return f"{text} SCT"
    return text


def augmented_tokens(tokens: list[str], text: str) -> list[str]:
    """Make SCT and its full expression mutually discoverable in lexical search."""
    if aliases_for(text):
        return list(dict.fromkeys([*tokens, "sct", "strefa", "czystego", "transportu"]))
    return tokens
