"""Pure domain types. Avoid importing FastAPI, ORM, and HTTP clients here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    FALSE = "false"
    MISLEADING = "misleading"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_VERIFIABLE = "not_verifiable"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXT = "context"


@dataclass(frozen=True)
class Claim:
    claim_id: str
    text: str
    kind: str = "verifiable"
    scope: str = "unknown"
    empirical_test: str = ""
    classification_reason: str = ""
    reviewed: bool = False


@dataclass(frozen=True)
class EventQuery:
    query_text: str
    entities: tuple[str, ...] = ()
    location: str | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None


@dataclass(frozen=True)
class DocumentCandidate:
    canonical_url: str
    publisher: str
    headline: str
    published_at: datetime | None
    provider: str


@dataclass(frozen=True)
class EvidencePassage:
    passage_id: str
    document_id: str
    exact_text: str
    publisher: str
    title: str
    url: str
    published_at: datetime | None
