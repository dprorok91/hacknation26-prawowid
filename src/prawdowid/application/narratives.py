"""Compare emphases among independent passages already matched to one event."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlsplit

from prawdowid.domain.models import EventQuery, EvidencePassage


@dataclass(frozen=True)
class Frame:
    passage: EvidencePassage
    event_id: str
    quote: str


def compare_narratives(event: EventQuery, matches: Sequence[EvidencePassage]) -> tuple[Frame, ...]:
    if not event.query_text:
        return ()
    event_key = "|".join(
        (
            event.query_text.strip().casefold(),
            *sorted(entity.casefold() for entity in event.entities),
            str(event.occurred_after),
            str(event.occurred_before),
        )
    )
    event_id = sha256(event_key.encode()).hexdigest()[:20]
    selected = []
    seen_publishers = set()
    seen_domains = set()
    prior_words = []
    for passage in matches:
        publisher = passage.publisher.strip().casefold()
        domain = (urlsplit(passage.url).hostname or "").removeprefix("www.")
        words = set(re.findall(r"[^\W_]+", passage.exact_text.casefold()))
        syndicated = any(
            len(words & previous) / len(words | previous) >= 0.8
            for previous in prior_words
            if words or previous
        )
        if not publisher or not domain or not words or syndicated:
            continue
        if publisher in seen_publishers or domain in seen_domains:
            continue
        seen_publishers.add(publisher)
        seen_domains.add(domain)
        prior_words.append(words)
        quote = passage.exact_text[:360].strip()
        selected.append(Frame(passage, event_id, quote))
        if len(selected) == 4:
            break
    if len(selected) < 2:
        return ()
    return tuple(sorted(selected, key=lambda frame: str(frame.passage.published_at or "")))
