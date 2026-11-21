import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa

from prawdowid.infrastructure.corpus import ArticleSnapshot, chunks, store_article
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import documents, passages, sources


def test_passage_offsets_preserve_literal_quotes():
    body = "Wodę znaleziono.\n\nMost pozostał otwarty."
    for start, end, text in chunks(body):
        assert body[start:end] == text
    assert len(list(chunks(body))) == 2


@pytest.mark.asyncio
async def test_metadata_policy_and_versioning_on_postgres():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")
    database = create_database(dsn)
    domain = f"{uuid4().hex}.example"
    url = f"https://{domain}/article"
    fetched = datetime.now(UTC)

    async def save(body, seconds):
        async with database.sessions.begin() as session:
            return await store_article(
                session,
                ArticleSnapshot(
                    url,
                    domain,
                    "Tytuł",
                    None,
                    fetched + timedelta(seconds=seconds),
                    body,
                ),
            )

    try:
        document_id = await save("Ukryty tekst źródła.", 0)
        async with database.sessions.begin() as session:
            assert (
                await session.scalar(
                    sa.select(sa.func.count())
                    .select_from(passages)
                    .where(passages.c.document_id == document_id)
                )
                == 0
            )
            await session.execute(
                sa.update(sources)
                .where(sources.c.domain == domain)
                .values(
                    enabled=True,
                    approved_at=fetched,
                    license_label="test license",
                )
            )
        same_id = await save("Treść wersji pierwszej.", 2)
        assert same_id == document_id
        assert await save("Treść wersji pierwszej.", 3) == document_id
        assert await save("Treść wersji drugiej i kolejne zdanie.", 4) == document_id
        assert await save("Stara treść nie powinna wygrać.", 1) == document_id
        repeats = await asyncio.gather(*[save("Treść wersji trzeciej.", 5) for _ in range(20)])
        assert set(repeats) == {document_id}
        async with database.sessions() as session:
            versions = (
                await session.execute(
                    sa.select(passages.c.content_version, passages.c.text)
                    .where(passages.c.document_id == document_id)
                    .order_by(passages.c.content_version)
                )
            ).all()
            assert versions == [
                (2, "Treść wersji pierwszej."),
                (3, "Treść wersji drugiej i kolejne zdanie."),
                (4, "Treść wersji trzeciej."),
            ]
    finally:
        async with database.sessions.begin() as session:
            await session.execute(sa.delete(documents).where(documents.c.canonical_url == url))
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()
