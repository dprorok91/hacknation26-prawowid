"""Public Pydantic contracts: five MSZ verdicts are separate from infrastructure errors."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from prawdowid.application.analyses import ErrorCode
from prawdowid.domain.urls import parse_http_url


class InputKind(str, Enum):
    TEXT = "text"
    URL = "url"


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: InputKind
    content: str = Field(min_length=1, max_length=20000, strict=True)
    language: Literal["pl"] = "pl"
    web_search: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def minimally_validate_format(self) -> AnalysisRequest:
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("content is required")
        if self.kind == InputKind.URL:
            if self.web_search:
                raise ValueError("web_search is only available for text; URLs are public by design")
            parse_http_url(self.content)
        return self


class ErrorDetail(BaseModel):
    error_code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class AuditResponse(BaseModel):
    at: datetime
    event_type: str
    model_name: str | None
    latency_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    estimated_cost_usd: float | None
    source_ids: list[str]
    error_code: str | None


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
    source_type: Literal["news", "primary", "factcheck"] = "news"
    title: str
    url: str
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    exact_quote: str | None = None
    supports_claim_ids: list[str] = Field(default_factory=list)


class ClaimResult(BaseModel):
    claim_id: str
    text: str
    claim_type: Literal["factual", "opinion", "fragment", "uncertain"] = "factual"
    evidence_scope: Literal["public", "private", "unknown"] = "unknown"
    span_start: int | None = Field(default=None, ge=0)
    span_end: int | None = Field(default=None, ge=0)
    assumptions: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    topic_links_count: int = 0
    empirical_test: str | None = None
    classification_reason: str | None = None
    classification_reviewed: bool = False
    verdict: Verdict
    rationale: str
    evidence_source_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class MediaFrame(BaseModel):
    claim_id: str | None = None
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


class DiscoveredLink(BaseModel):
    """Public discovery metadata, never treated as verified evidence."""

    title: str
    publisher: str
    url: str
    provider: str
    published_at: datetime | None = None
    category: Literal["news", "fact_check"] = "news"
    claim_ids: list[str] = Field(default_factory=list)


class DiscoveryProviderError(BaseModel):
    provider: str
    code: str
    claim_id: str | None = None


class ClaimSearchStatus(BaseModel):
    claim_id: str
    status: Literal["attempted", "sufficient_local_sources", "budget_exhausted"]


class DiscoveryStatus(BaseModel):
    requested: bool = False
    attempted: bool = False
    status: Literal[
        "disabled", "skipped_no_public_claims", "sufficient_local_sources",
        "providers_partial", "completed",
    ] = "disabled"
    candidates_found: int = 0
    provider_errors: list[DiscoveryProviderError] = Field(default_factory=list)
    per_claim: list[ClaimSearchStatus] = Field(default_factory=list)
    queries: list[dict[str, str]] = Field(default_factory=list)


class PriorFactCheck(BaseModel):
    """Published third-party judgment, NOT a PRAWDOWID verdict or evidence citation."""

    claim_id: str
    publisher: str
    title: str
    url: str
    reviewed_claim: str | None = None
    published_rating: str | None = None
    reviewed_at: str | None = None
    provider: str
    match_status: Literal["not_assessed"] = "not_assessed"


class Coverage(BaseModel):
    evidence_publishers: list[str] = Field(default_factory=list)
    fact_check_evidence_publishers: list[str] = Field(default_factory=list)
    topic_publishers: list[str] = Field(default_factory=list)
    fact_check_candidates: list[DiscoveredLink] = Field(default_factory=list)
    note: str | None = None


class AnalysisReport(BaseModel):
    analysis_id: str
    claims: list[ClaimResult] = Field(default_factory=list)
    narratives: list[MediaFrame] = Field(default_factory=list)
    articles: list[ArticleSummary] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    discovered_links: list[DiscoveredLink] = Field(default_factory=list)
    search: DiscoveryStatus = Field(default_factory=DiscoveryStatus)
    coverage: Coverage = Field(default_factory=Coverage)
    fact_checks: list[PriorFactCheck] = Field(default_factory=list)
    fact_check_errors: list[str] = Field(default_factory=list)
    annotated_text: str | None = None
    source_snapshot: str | None = None


class AnalysisResponse(BaseModel):
    analysis_id: str
    status: Literal["queued", "processing", "completed", "failed", "needs_clarification"]
    report: AnalysisReport | None = None
    error_code: str | None = None
    retry_after_seconds: int | None = None
