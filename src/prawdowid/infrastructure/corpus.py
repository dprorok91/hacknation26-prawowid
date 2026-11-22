"""Versioned, policy-gated document ingestion."""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from prawdowid.domain.models import DocumentCandidate
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.db_models import document_versions, documents, passages, sources


@dataclass(frozen=True)
class ArticleSnapshot:
    url: str
    publisher: str
    title: str
    published_at: datetime | None
    fetched_at: datetime
    body: str | None = None


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def chunks(body: str) -> Iterator[tuple[int, int, str]]:
    for paragraph in re.finditer(r"[^\n]+", body):
        words = tuple(re.finditer(r"\S+", paragraph.group()))
        for offset in range(0, len(words), 280):
            start = paragraph.start() + words[offset].start()
            end = paragraph.start() + words[min(offset + 279, len(words) - 1)].end()
            yield start, end, body[start:end]


async def approved_license(session: AsyncSession, domain: str) -> str | None:
    return await session.scalar(
        sa.select(sources.c.license_label).where(
            sources.c.domain == domain,
            sources.c.enabled.is_(True),
            sources.c.approved_at.is_not(None),
        )
    )


async def store_article(
    session: AsyncSession,
    article: ArticleSnapshot,
    *,
    vectors: Sequence[Sequence[float]] | None = None,
    model_id: str | None = None,
) -> str:
    url = canonical_url(article.url)
    domain = urlsplit(url).hostname
    await session.execute(
        insert(sources)
        .values(domain=domain, display_name=article.publisher or domain, source_type="news")
        .on_conflict_do_nothing(index_elements=[sources.c.domain])
    )
    source = (
        await session.execute(
            sa.select(sources).where(sources.c.domain == domain).with_for_update()
        )
    ).one()
    license_label = (
        source.license_label if source.enabled and source.approved_at is not None else None
    )
    body = article.body if license_label else None
    body_hash = digest(body) if body else None
    if not body:
        vectors = None

    document_id = str(uuid4())
    fields = {
        "title": article.title,
        "published_at": article.published_at,
        "fetched_at": article.fetched_at,
        "content_hash": body_hash,
        "text_license": license_label if body else None,
        "retrieval_status": "ready" if vectors else "text_only" if body else "metadata_only",
        "expires_at": article.fetched_at + timedelta(days=7),
    }
    inserted = await session.scalar(
        insert(documents)
        .values(
            id=document_id,
            source_id=source.id,
            canonical_url=url,
            url_hash=digest(url),
            **fields,
        )
        .on_conflict_do_nothing(index_elements=[documents.c.canonical_url])
        .returning(documents.c.id)
    )
    if inserted is None:
        current = (
            await session.execute(
                sa.select(documents).where(documents.c.canonical_url == url).with_for_update()
            )
        ).one()
        document_id = current.id
        if current.fetched_at >= article.fetched_at:
            return document_id
        if (
            current.content_hash == body_hash
            and current.title == article.title
            and (
                current.retrieval_status == fields["retrieval_status"]
                or current.retrieval_status == "ready"
            )
        ):
            await session.execute(
                sa.update(documents)
                .where(documents.c.id == current.id)
                .values(fetched_at=article.fetched_at, expires_at=fields["expires_at"])
            )
            await session.execute(
                sa.update(document_versions)
                .where(
                    document_versions.c.document_id == current.id,
                    document_versions.c.content_version == current.content_version,
                )
                .values(expires_at=fields["expires_at"])
            )
            return document_id
        version = current.content_version + 1
        await session.execute(
            sa.update(documents)
            .where(documents.c.id == current.id)
            .values(
                content_version=version, indexed_at=article.fetched_at if body else None, **fields
            )
        )
    else:
        version = 1
        if body:
            await session.execute(
                sa.update(documents)
                .where(documents.c.id == document_id)
                .values(indexed_at=article.fetched_at)
            )

    await session.execute(
        sa.insert(document_versions).values(
            document_id=document_id, content_version=version, **fields
        )
    )
    if body:
        spans = list(chunks(body))
        if vectors is not None and (len(vectors) != len(spans) or not model_id):
            raise ValueError("Passage vectors do not match the document")
        rows = [
            {
                "document_id": document_id,
                "content_version": version,
                "ordinal": ordinal,
                "char_start": start,
                "char_end": end,
                "text": text,
                "source_span_hash": digest(text),
                "embedding": list(vectors[ordinal]) if vectors is not None else None,
                "embedding_model_id": model_id if vectors is not None else None,
            }
            for ordinal, (start, end, text) in enumerate(spans)
        ]
        if rows:
            await session.execute(sa.insert(passages), rows)
    return document_id


async def store_discovered_metadata(session: AsyncSession, item: DocumentCandidate) -> str | None:
    """Save a discovered link; NEVER replace previously approved indexed text.

    Discovery is untrusted metadata, not citable evidence. Both insertions are
    idempotent; fetch workers can independently promote eligible documents.
    """
    url = canonical_url(item.canonical_url)
    domain = urlsplit(url).hostname
    await session.execute(
        insert(sources).values(
            domain=domain, display_name=(item.publisher or domain)[:500], source_type="news"
        ).on_conflict_do_nothing(index_elements=[sources.c.domain])
    )
    source_id = await session.scalar(sa.select(sources.c.id).where(sources.c.domain == domain))
    document_id = str(uuid4())
    fetched = datetime.now(UTC)
    inserted = await session.scalar(
        insert(documents).values(
            id=document_id, source_id=source_id,
            canonical_url=url, url_hash=digest(url),
            title=(item.headline or "Untitled")[:500],
            published_at=item.published_at,
            fetched_at=fetched, retrieval_status="metadata_only",
            expires_at=fetched + timedelta(days=7),
        ).on_conflict_do_nothing(index_elements=[documents.c.canonical_url])
        .returning(documents.c.id)
    )
    if inserted is None:
        return await session.scalar(
            sa.select(documents.c.id).where(documents.c.canonical_url == url)
        )
    await session.execute(sa.insert(document_versions).values(
        document_id=inserted, content_version=1,
        title=(item.headline or "Untitled")[:500],
        published_at=item.published_at,
        fetched_at=fetched, retrieval_status="metadata_only",
        expires_at=fetched + timedelta(days=7),
    ))
    return inserted
