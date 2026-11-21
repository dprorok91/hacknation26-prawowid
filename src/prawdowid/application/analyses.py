"""Transport-independent analysis submission and polling contract."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED_MEDIA = "unsupported_media"
    SOURCE_UNAVAILABLE = "source_unavailable"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"
    NOT_FOUND = "not_found"
    ANALYSIS_NOT_READY = "analysis_not_ready"


class AnalysisError(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Submission:
    kind: str
    content: str
    queued_at: datetime | None = None


@dataclass(frozen=True)
class AnalysisOutcome:
    analysis_id: str
    status: str
    report: dict | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None
    delete_token: str | None = None


@dataclass(frozen=True)
class AnalysisAudit:
    at: datetime
    event_type: str
    model_name: str | None
    latency_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    estimated_cost_usd: float | None
    source_ids: list[str]
    error_code: str | None


class AnalysisService(Protocol):
    async def submit(self, request: Submission) -> AnalysisOutcome: ...

    async def get(self, analysis_id: str) -> AnalysisOutcome | None: ...

    async def get_public(self, analysis_id: str) -> AnalysisOutcome | None: ...

    async def delete(self, analysis_id: str, token: str | None) -> bool: ...

    async def audit(self, analysis_id: str) -> AnalysisAudit | None: ...


class UnavailableAnalyses:
    async def submit(self, request: Submission) -> AnalysisOutcome:
        raise AnalysisError(ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable.")

    async def get(self, analysis_id: str) -> AnalysisOutcome | None:
        raise AnalysisError(ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable.")

    async def get_public(self, analysis_id: str) -> AnalysisOutcome | None:
        return await self.get(analysis_id)

    async def delete(self, analysis_id: str, token: str | None) -> bool:
        await self.get(analysis_id)
        return False

    async def audit(self, analysis_id: str) -> AnalysisAudit | None:
        await self.get(analysis_id)
        return None
