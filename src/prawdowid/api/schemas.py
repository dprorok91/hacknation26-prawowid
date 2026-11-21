"""Public Pydantic contracts: five MSZ verdicts are separate from infrastructure errors."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class InputKind(str, Enum):
    TEXT = "text"
    URL = "url"


class AnalysisRequest(BaseModel):
    kind: InputKind
    content: str = Field(min_length=1, max_length=20000)
    language: Literal["pl"] = "pl"

    @model_validator(mode="after")
    def minimally_validate_format(self) -> "AnalysisRequest":
        # Security-sensitive URL checks/SSRF are NOT implemented yet; see task P0-05.
        if self.kind == InputKind.URL and not self.content.strip().startswith(("https://", "http://")):
            raise ValueError("URL input must begin with https:// or http://")
        return self


class Verdict(str, Enum):
    CONFIRMED = "confirmed"
    FALSE = "false"
    MISLEADING = "misleading"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_VERIFIABLE = "not_verifiable"


class SourceReference(BaseModel):
    source_id: str
    passage_id: str | None = None
    publisher: str
    title: str
    url: str
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    exact_quote: str | None = None
    supports_claim_ids: list[str] = Field(default_factory=list)


class ClaimResult(BaseModel):
    claim_id: str
    text: str
    verdict: Verdict
    rationale: str
    evidence_source_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MediaFrame(BaseModel):
    source_id: str
    event_id: str
    emphasis: list[str]
    highlighted_passage_ids: list[str]
    caveat: str | None = None


class ArticleSummary(BaseModel):
    source_id: str
    headline: str
    summary: str
    metadata_only: bool = False


class AnalysisReport(BaseModel):
    analysis_id: str
    claims: list[ClaimResult] = Field(default_factory=list)
    narratives: list[MediaFrame] = Field(default_factory=list)
    articles: list[ArticleSummary] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    source_snapshot: str | None = None


class AnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued", "processing", "completed", "failed", "needs_clarification"]
    report: AnalysisReport | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None
