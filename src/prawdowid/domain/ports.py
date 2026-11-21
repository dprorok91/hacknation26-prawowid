"""Protocols define boundaries; implementation belongs in infrastructure/adapters."""
from __future__ import annotations

from typing import Any, Protocol, Sequence

from prawdowid.domain.models import Claim, DocumentCandidate, EventQuery, EvidencePassage


class SourceDiscovery(Protocol):
    async def discover(self, event: EventQuery, limit: int) -> Sequence[DocumentCandidate]: ...


class DocumentFetcher(Protocol):
    async def fetch(self, url: str) -> bytes: ...


class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class PassageRepository(Protocol):
    async def search_text(self, query: str, limit: int) -> Sequence[EvidencePassage]: ...

    async def search_vector(
        self, vector: Sequence[float], model_id: str, limit: int
    ) -> Sequence[EvidencePassage]: ...


class JobRepository(Protocol):
    async def enqueue(self, kind: str, dedupe_key: str, payload: dict[str, Any]) -> str: ...

    async def lease_next(self, worker_id: str) -> dict[str, Any] | None: ...

    async def complete(self, job_id: str, worker_id: str) -> bool: ...


class ClaimExtractor(Protocol):
    async def extract(self, text: str) -> tuple[EventQuery, Sequence[Claim]]: ...


class EvidenceRetriever(Protocol):
    async def retrieve(self, event: EventQuery, claims: Sequence[Claim]) -> dict[str, list[EvidencePassage]]: ...


class ClaimVerifier(Protocol):
    async def verify(self, claim: Claim, evidence: Sequence[EvidencePassage]) -> dict[str, Any]: ...


class NarrativeComparer(Protocol):
    async def compare(self, event: EventQuery, passages: Sequence[EvidencePassage]) -> list[dict[str, Any]]: ...


class StructuredLLM(Protocol):
    async def complete_json(self, *, schema: dict[str, Any], prompt: str) -> dict[str, Any]: ...
