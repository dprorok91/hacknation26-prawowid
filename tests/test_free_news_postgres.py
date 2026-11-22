"""Integration tests run only against a disposable migrated PostgreSQL database."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa

from prawdowid.domain.models import DocumentCandidate, EventQuery
from prawdowid.infrastructure.corpus import (
    ArticleSnapshot,
    store_article,
    store_discovered_metadata,
)
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import (
    discovery_cache,
    document_versions,
    documents,
    passages,
    provider_daily_usage,
    sources,
)
from prawdowid.infrastructure.discovery import DiscoveredArticle
from prawdowid.infrastructure.discovery_store import PostgresDailyBudget, PostgresDiscoveryCache


@pytest.mark.asyncio
async def test_discovery_cache_and_atomic_daily_credits():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Use migrated disposable PostgreSQL TEST_DATABASE_URL")
    database = create_database(dsn)
    provider = "newsdata-" + uuid4().hex
    event = EventQuery("Wydarzenie testowe " + uuid4().hex)
    candidate = DiscoveredArticle(
        "https://example.pl/a", "Publisher", "Headline", None, provider, "example.pl"
    )
    cache = PostgresDiscoveryCache(database.sessions, hours=12)
    budget = PostgresDailyBudget(database.sessions, limit=3)
    try:
        assert await cache.get(provider, event) is None
        await cache.put(provider, event, [candidate])
        restored = await cache.get(provider, event)
        assert restored == (candidate,)
        assert await cache.get(provider, EventQuery("Different query")) is None
        claims = await asyncio.gather(*(budget.claim(provider) for _ in range(10)))
        assert sum(claims) == 3
    finally:
        async with database.sessions.begin() as session:
            await session.execute(sa.delete(discovery_cache).where(discovery_cache.c.provider == provider))
            await session.execute(sa.delete(provider_daily_usage).where(provider_daily_usage.c.provider == provider))
        await database.dispose()


@pytest.mark.asyncio
async def test_metadata_only_does_not_replace_approved_source_passages():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Use migrated disposable PostgreSQL TEST_DATABASE_URL")
    database = create_database(dsn)
    domain = uuid4().hex + ".example"
    url = f"https://{domain}/article"
    now = datetime.now(UTC)
    try:
        async with database.sessions.begin() as session:
            await session.execute(sa.insert(sources).values(
                domain=domain, display_name="Fixture", source_type="primary",
                enabled=True, approved_at=now, license_label="fixture",
            ))
            doc_id = await store_article(
                session, ArticleSnapshot(url, "Fixture", "Original headline", now, now,
                                         "Tekst o wydarzeniu podany przez urzad."),
            )
            assert await store_discovered_metadata(session, DocumentCandidate(
                url, "News", "Wrong headline", now, "newsdata"
            )) == doc_id
        async with database.sessions() as session:
            row = (await session.execute(sa.select(documents).where(documents.c.id == doc_id))).one()
            assert row.title == "Original headline"
            assert row.retrieval_status == "text_only"
            assert await session.scalar(sa.select(sa.func.count()).select_from(passages).where(
                passages.c.document_id == doc_id
            )) == 1
            assert await session.scalar(sa.select(sa.func.count()).select_from(document_versions).where(
                document_versions.c.document_id == doc_id
            )) == 1
    finally:
        async with database.sessions.begin() as session:
            await session.execute(sa.delete(documents).where(documents.c.canonical_url == url))
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()


@pytest.mark.asyncio
async def test_new_discovered_link_stores_no_passages():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Use migrated disposable PostgreSQL TEST_DATABASE_URL")
    database = create_database(dsn)
    domain = uuid4().hex + ".example"
    url = f"https://{domain}/new"
    try:
        async with database.sessions.begin() as session:
            candidate = DocumentCandidate(url, "News", "Discovered", None, "gdelt")
            first = await store_discovered_metadata(session, candidate)
            second = await store_discovered_metadata(session, candidate)
            assert first == second
        async with database.sessions() as session:
            row = (await session.execute(sa.select(documents).where(documents.c.id == first))).one()
            assert row.retrieval_status == "metadata_only"
            assert await session.scalar(sa.select(sa.func.count()).select_from(passages).where(
                passages.c.document_id == first
            )) == 0
    finally:
        async with database.sessions.begin() as session:
            await session.execute(sa.delete(documents).where(documents.c.canonical_url == url))
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()
