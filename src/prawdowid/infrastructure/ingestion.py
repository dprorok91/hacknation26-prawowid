"""Fetch eligible web articles before opening the write transaction."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.application.worker import JobFailure, JobLease
from prawdowid.domain.models import DocumentCandidate
from prawdowid.domain.ports import Embedder
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import (
    ArticleSnapshot,
    approved_license,
    chunks,
    digest,
    store_article,
)
from prawdowid.infrastructure.db_models import documents, jobs, passages, sources
from prawdowid.infrastructure.job_queue import insert_job
from prawdowid.infrastructure.safe_web import FetchError, SafeWeb, extract_article

FETCH_URL = "FETCH_URL"


@dataclass(frozen=True)
class IngestedArticle:
    url: str
    title: str
    published_at: datetime | None
    text: str


async def enqueue_article(
    session: AsyncSession, url: str, *, title: str = "", publisher: str = ""
) -> str:
    url = canonical_url(url)
    dedupe_key = digest(url)
    previous = (
        await session.execute(
            sa.select(jobs.c.id, jobs.c.generation, jobs.c.state, jobs.c.updated_at)
            .where(jobs.c.kind == FETCH_URL, jobs.c.dedupe_key == dedupe_key)
            .order_by(jobs.c.generation.desc())
            .limit(1)
        )
    ).one_or_none()
    if previous is not None:
        cooldown = timedelta(hours=1) if previous.state == "completed" else timedelta(minutes=1)
        if previous.state in ("queued", "leased") or previous.updated_at + cooldown > datetime.now(
            UTC
        ):
            return previous.id
    return await insert_job(
        session,
        kind=FETCH_URL,
        dedupe_key=dedupe_key,
        generation=previous.generation + 1 if previous is not None else 0,
        payload={"url": url, "title": title, "publisher": publisher},
    )


async def article_job_result(
    session: AsyncSession, job_id: str
) -> tuple[str, IngestedArticle | None]:
    row = (
        await session.execute(
            sa.select(jobs.c.state, jobs.c.payload_json).where(jobs.c.id == job_id)
        )
    ).one_or_none()
    if row is None or row.state != "completed":
        return (row.state if row else "dead"), None
    document_id = row.payload_json.get("document_id")
    document = (
        await session.execute(
            sa.select(documents)
            .join(sources, documents.c.source_id == sources.c.id)
            .where(
                documents.c.id == document_id,
                documents.c.expires_at > sa.func.now(),
                documents.c.retrieval_status.in_(("ready", "text_only")),
                sources.c.enabled.is_(True),
                sources.c.approved_at.is_not(None),
                sources.c.license_label != "",
                documents.c.text_license == sources.c.license_label,
            )
        )
    ).one_or_none()
    if document is None:
        return "completed", None
    text = (
        await session.scalars(
            sa.select(passages.c.text)
            .join(documents, passages.c.document_id == documents.c.id)
            .join(sources, documents.c.source_id == sources.c.id)
            .where(
                passages.c.document_id == document.id,
                passages.c.content_version == document.content_version,
                passages.c.content_version == documents.c.content_version,
                documents.c.expires_at > sa.func.now(),
                sources.c.enabled.is_(True),
                sources.c.approved_at.is_not(None),
                sources.c.license_label != "",
                documents.c.text_license == sources.c.license_label,
            )
            .order_by(passages.c.ordinal)
        )
    ).all()
    return "completed", IngestedArticle(
        document.canonical_url, document.title, document.published_at, "\n\n".join(text)
    )


class CorpusAccess:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def article(self, url: str) -> IngestedArticle:
        async with self.sessions.begin() as session:
            job = await enqueue_article(session, url)
        async with asyncio.timeout(9):
            while True:
                async with self.sessions() as session:
                    state, article = await article_job_result(session, job)
                if state in {"dead", "completed"}:
                    if article is None or not article.text:
                        raise AnalysisError(
                            ErrorCode.SOURCE_UNAVAILABLE,
                            "Article text is unavailable. You can submit a quoted statement.",
                        )
                    return article
                await asyncio.sleep(0.2)

    async def enrich(self, candidates: Sequence[DocumentCandidate]) -> bool:
        async with self.sessions.begin() as session:
            job_ids = [
                await enqueue_article(
                    session,
                    item.canonical_url,
                    title=item.headline,
                    publisher=item.publisher,
                )
                for item in sorted(candidates[:4], key=lambda item: item.canonical_url)
            ]
        if not job_ids:
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 4
        while loop.time() < deadline:
            async with self.sessions() as session:
                states = (
                    await session.scalars(sa.select(jobs.c.state).where(jobs.c.id.in_(job_ids)))
                ).all()
            if "completed" in states:
                return True
            if states and all(state == "dead" for state in states):
                return False
            await asyncio.sleep(0.25)
        return False


class ArticleIngestion:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        web: SafeWeb,
        embedder: Embedder | None = None,
    ):
        self.sessions = sessions
        self.web = web
        self.embedder = embedder

    async def __call__(self, lease: JobLease):
        fetched_at = datetime.now(UTC)
        try:
            page = await self.web.read_article(lease.payload["url"])
        except FetchError as exc:
            timeout = exc.reason == "timeout"
            raise JobFailure(
                ErrorCode.TIMEOUT if timeout else ErrorCode.SOURCE_UNAVAILABLE,
                retryable=timeout or exc.reason == "source_unavailable",
            ) from None
        domain = urlsplit(page.url).hostname
        async with self.sessions() as session:
            policy = await approved_license(session, domain)
        article = extract_article(page.content, allow_text=bool(policy), robots=page.robots)
        title = article.title or lease.payload.get("title") or domain
        snapshot = ArticleSnapshot(
            page.url,
            lease.payload.get("publisher") or domain,
            title,
            article.published_at,
            fetched_at,
            article.body,
        )
        model_id = None
        vectors = None
        if self.embedder is not None and article.body:
            spans = [text for _, _, text in chunks(article.body)]
            if spans:
                try:
                    vectors = await self.embedder.embed_passages(spans)
                    model_id = self.embedder.model_id
                except (OSError, RuntimeError, ValueError):
                    # Licensed passages can still be indexed by FTS.
                    pass

        async def persist(session):
            document_id = await store_article(session, snapshot, vectors=vectors, model_id=model_id)
            await session.execute(
                sa.update(jobs)
                .where(jobs.c.id == lease.id)
                .values(payload_json={**lease.payload, "document_id": document_id})
            )

        return persist
