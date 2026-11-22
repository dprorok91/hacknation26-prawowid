"""Atomic PostgreSQL analysis submission and lifecycle."""

import re
import secrets
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from hmac import compare_digest
from ipaddress import ip_address
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prawdowid.application.analyses import (
    AnalysisAudit,
    AnalysisError,
    AnalysisOutcome,
    ErrorCode,
    Submission,
)
from prawdowid.application.worker import JobLease
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import approved_license
from prawdowid.infrastructure.db_models import (
    analyses,
    audit_events,
    document_versions,
    documents,
    jobs,
    sources,
)
from prawdowid.infrastructure.ingestion import enqueue_article
from prawdowid.infrastructure.job_queue import insert_job
from prawdowid.infrastructure.reports import evidence_rows, save_report

PIPELINE_VERSION = "v1"
ANALYZE = "ANALYZE"
EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PHONE = re.compile(r"(?<!\w)(?:\+48[ -]?)?\d{3}[ -]\d{3}[ -]\d{3}(?!\w)")
SENSITIVE_QUERY = re.compile(
    r"token|secret|password|signature|\b(?:key|sig|auth|session)\b", re.IGNORECASE
)
PRIVATE_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".home.arpa")


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _submission_value(request: Submission) -> str:
    if request.kind == "text":
        return PHONE.sub("[telefon]", EMAIL.sub("[e-mail]", request.content))
    try:
        value = canonical_url(request.content)
        url = urlsplit(value)
        if (
            not url.hostname
            or url.port not in (None, 80, 443)
            or any(SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(url.query))
        ):
            raise ValueError
        try:
            address = ip_address(url.hostname)
        except ValueError:
            if "." not in url.hostname or url.hostname.endswith(PRIVATE_HOST_SUFFIXES):
                raise ValueError from None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError
    except ValueError:
        raise AnalysisError(ErrorCode.INVALID_INPUT, "Provide a public article URL.") from None
    return value


def _outcome(row, *, delete_token=None) -> AnalysisOutcome:
    return AnalysisOutcome(
        analysis_id=str(row.id),
        status=row.status,
        report=row.response_json,
        error_code=row.error_code,
        retry_after_seconds=1 if row.status in ("queued", "processing") else None,
        delete_token=delete_token,
    )


class PostgresAnalyses:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        retention_hours: int = 24,
        text_retention_minutes: int = 15,
    ):
        self.sessions = sessions
        self.retention = timedelta(hours=retention_hours)
        self.text_retention = timedelta(minutes=text_retention_minutes)

    async def submit(self, request: Submission) -> AnalysisOutcome:
        value = _submission_value(request)
        new_id = str(uuid4())
        token = secrets.token_urlsafe(32)
        try:
            async with self.sessions.begin() as session:
                public_cache = False
                if request.kind == "url":
                    parts = urlsplit(value)
                    if not parts.query:
                        public_cache = bool(await approved_license(session, parts.hostname))
                input_hash = _hash(value) if public_cache else _hash(new_id)
                public_model_allowed = (
                    public_cache
                    and request.public_model_allowed is True
                    and "?" not in request.content
                    and "#" not in request.content
                )
                # An in-flight analysis is reusable only within the same disclosure boundary.
                cache_key = None
                if public_cache:
                    scope = "public-model" if public_model_allowed else "local-model"
                    cache_key = _hash(f"{PIPELINE_VERSION}:{scope}:{input_hash}")
                if cache_key is not None:
                    await session.execute(
                        sa.update(analyses)
                        .where(
                            analyses.c.cache_key == cache_key,
                            analyses.c.expires_at <= sa.func.now(),
                        )
                        .values(cache_key=None)
                    )
                statement = (
                    insert(analyses)
                    .values(
                        id=new_id,
                        input_hash=input_hash,
                        input_type=request.kind,
                        input_redacted=value,
                        pipeline_version=PIPELINE_VERSION,
                        cache_key=cache_key,
                        delete_token_hash=_hash(token),
                        expires_at=sa.func.now()
                        + (self.text_retention if request.kind == "text" else self.retention),
                    )
                    .on_conflict_do_update(
                        index_elements=[analyses.c.cache_key], set_={"cache_key": cache_key}
                    )
                    .returning(analyses)
                )
                row = (await session.execute(statement)).one()
                if row.id == new_id:
                    await insert_job(
                        session,
                        kind=ANALYZE,
                        dedupe_key=str(row.id),
                        payload={
                            "analysis_id": str(row.id),
                            "public_model_allowed": public_model_allowed,
                            "web_search": request.kind == "text" and request.web_search,
                        },
                    )
                    if request.kind == "url":
                        await enqueue_article(session, value)
                return _outcome(row, delete_token=token if row.id == new_id else None)
        except (DBAPIError, PoolTimeout, OSError):
            raise AnalysisError(
                ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable."
            ) from None

    async def get(self, analysis_id: str) -> AnalysisOutcome | None:
        return await self._read(analysis_id, public=False)

    async def get_public(self, analysis_id: str) -> AnalysisOutcome | None:
        return await self._read(analysis_id, public=True)

    async def delete(self, analysis_id: str, token: str | None) -> bool:
        if not token or len(token) > 128:
            return False
        try:
            analysis_id = str(UUID(analysis_id))
        except ValueError:
            return False
        try:
            async with self.sessions.begin() as session:
                expected = await session.scalar(
                    sa.select(analyses.c.delete_token_hash).where(analyses.c.id == analysis_id)
                )
                if expected is None or not compare_digest(expected, _hash(token)):
                    return False
                await _remove_analyses(session, [analysis_id])
                return True
        except (DBAPIError, PoolTimeout, OSError):
            raise AnalysisError(
                ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable."
            ) from None

    async def audit(self, analysis_id: str) -> AnalysisAudit | None:
        if not await self.get(analysis_id):
            return None
        try:
            async with self.sessions() as session:
                row = (
                    await session.execute(
                        sa.select(audit_events)
                        .where(audit_events.c.analysis_id == analysis_id)
                        .order_by(audit_events.c.at.desc())
                        .limit(1)
                    )
                ).one_or_none()
                if row is None:
                    return None
                return AnalysisAudit(
                    row.at,
                    row.event_type,
                    row.model_name,
                    row.latency_ms,
                    row.tokens_in,
                    row.tokens_out,
                    float(row.estimated_cost_usd) if row.estimated_cost_usd is not None else None,
                    row.source_ids_json or [],
                    (row.redacted_metadata_json or {}).get("error_code"),
                )
        except (DBAPIError, PoolTimeout, OSError):
            raise AnalysisError(
                ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable."
            ) from None

    async def _read(self, analysis_id: str, *, public: bool) -> AnalysisOutcome | None:
        try:
            analysis_id = str(UUID(analysis_id))
        except ValueError:
            return None
        try:
            async with self.sessions() as session:
                row = (
                    await session.execute(
                        sa.select(analyses).where(
                            analyses.c.id == analysis_id, analyses.c.expires_at > sa.func.now()
                        )
                    )
                ).one_or_none()
                if row is None:
                    return None
                if public:
                    if (
                        row.input_type != "url"
                        or row.status != "completed"
                        or not (row.response_json or {}).get("sources")
                    ):
                        return None
                    approved = await session.scalar(
                        sa.select(documents.c.id)
                        .join(sources, documents.c.source_id == sources.c.id)
                        .where(
                            documents.c.url_hash == row.input_hash,
                            documents.c.expires_at > sa.func.now(),
                            documents.c.retrieval_status.in_(("ready", "text_only")),
                            sources.c.enabled.is_(True),
                            sources.c.approved_at.is_not(None),
                            sources.c.license_label != "",
                        )
                        .limit(1)
                    )
                    if not approved:
                        return None
                if row.response_json and await evidence_rows(session, row.response_json) is None:
                    if public:
                        return None
                    return AnalysisOutcome(
                        row.id, "failed", error_code=ErrorCode.SOURCE_UNAVAILABLE
                    )
                return _outcome(row)
        except (DBAPIError, PoolTimeout, OSError):
            raise AnalysisError(
                ErrorCode.SOURCE_UNAVAILABLE, "Analysis storage is unavailable."
            ) from None


async def claim_analysis_input(session: AsyncSession, lease: JobLease) -> Submission | None:
    active = await session.scalar(
        sa.select(jobs.c.id)
        .where(
            jobs.c.id == lease.id,
            jobs.c.state == "leased",
            jobs.c.lease_owner == lease.worker_id,
            jobs.c.attempts == lease.attempt,
            jobs.c.lease_until > sa.func.clock_timestamp(),
        )
        .with_for_update()
    )
    if active is None:
        return None
    record = (
        await session.execute(
            sa.update(analyses)
            .where(
                analyses.c.id == lease.payload["analysis_id"],
                analyses.c.status.in_(("queued", "processing")),
                analyses.c.expires_at > sa.func.now(),
            )
            .values(status="processing", updated_at=sa.func.now())
            .returning(analyses.c.input_type, analyses.c.input_redacted, analyses.c.created_at)
        )
    ).one_or_none()
    if record is None or record.input_redacted is None:
        return None
    public_model_allowed = False
    if record.input_type == "url" and lease.payload.get("public_model_allowed") is True:
        parts = urlsplit(record.input_redacted)
        if parts.hostname and not parts.query and not parts.fragment:
            public_model_allowed = bool(await approved_license(session, parts.hostname))
    return Submission(
        record.input_type,
        record.input_redacted,
        record.created_at,
        web_search=lease.payload.get("web_search") is True,
        public_model_allowed=public_model_allowed,
    )


async def finish_analysis(
    session: AsyncSession,
    analysis_id: str,
    *,
    report: dict | None = None,
    error_code: str | None = None,
    model_name: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    estimated_cost_usd: Decimal | None = Decimal(0),
) -> None:
    if (report is None) == (error_code is None):
        raise ValueError("A finished analysis needs exactly one report or error code")
    evidence_links = await evidence_rows(session, report) if report is not None else {}
    if evidence_links is None:
        report = None
        error_code = ErrorCode.SOURCE_UNAVAILABLE
    values = {
        "status": "failed" if error_code else "completed",
        "response_json": report,
        "error_code": error_code,
        "corpus_snapshot": report.get("source_snapshot") if report else None,
        "input_redacted": None,
        "updated_at": sa.func.now(),
        "finished_at": sa.func.now(),
        "cache_key": None,
    }
    statement = (
        sa.update(analyses)
        .where(
            analyses.c.id == analysis_id,
            analyses.c.status.in_(("queued", "processing")),
            analyses.c.expires_at > sa.func.clock_timestamp(),
        )
        .values(**values)
        .returning(analyses.c.id, analyses.c.created_at, analyses.c.finished_at)
    )
    finished = (await session.execute(statement)).one_or_none()
    if finished is None:
        return
    if report is not None:
        await save_report(session, finished.id, report, evidence_links)
    await session.execute(
        sa.insert(audit_events).values(
            analysis_id=finished.id,
            event_type=values["status"],
            model_name=model_name,
            latency_ms=int((finished.finished_at - finished.created_at).total_seconds() * 1000),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            estimated_cost_usd=estimated_cost_usd if model_name else None,
            source_ids_json=sorted(evidence_links or ()),
            redacted_metadata_json={"error_code": error_code} if error_code else {},
        )
    )


async def _remove_analyses(session: AsyncSession, ids: list[str]) -> None:
    await session.execute(sa.delete(jobs).where(jobs.c.kind == ANALYZE, jobs.c.dedupe_key.in_(ids)))
    await session.execute(sa.delete(audit_events).where(audit_events.c.analysis_id.in_(ids)))
    await session.execute(sa.delete(analyses).where(analyses.c.id.in_(ids)))


async def prune_expired(session: AsyncSession) -> bool:
    ids = list(
        await session.scalars(
            sa.select(analyses.c.id)
            .where(analyses.c.expires_at <= sa.func.now())
            .order_by(analyses.c.expires_at)
            .limit(100)
        )
    )
    if ids:
        await _remove_analyses(session, ids)
    old_fetches = (
        sa.select(jobs.c.id)
        .where(
            jobs.c.kind != ANALYZE,
            jobs.c.state.in_(("completed", "dead")),
            jobs.c.updated_at <= sa.func.now() - timedelta(hours=2),
        )
        .limit(100)
    )
    await session.execute(sa.delete(jobs).where(jobs.c.id.in_(old_fetches)))
    old_documents = (
        sa.select(documents.c.id).where(documents.c.expires_at <= sa.func.now()).limit(100)
    )
    await session.execute(sa.delete(documents).where(documents.c.id.in_(old_documents)))
    old_versions = (
        sa.select(document_versions.c.document_id, document_versions.c.content_version)
        .join(documents, document_versions.c.document_id == documents.c.id)
        .where(
            document_versions.c.content_version < documents.c.content_version,
            document_versions.c.expires_at <= sa.func.now(),
        )
        .limit(100)
    )
    await session.execute(
        sa.delete(document_versions).where(
            sa.tuple_(document_versions.c.document_id, document_versions.c.content_version).in_(
                old_versions
            )
        )
    )
    return len(ids) == 100
