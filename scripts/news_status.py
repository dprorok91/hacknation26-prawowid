"""Print only public news-ingestion totals and today's local NewsData credit usage."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime

import sqlalchemy as sa

from prawdowid.config import get_settings
from prawdowid.domain.source_flags import classify_source
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import (
    discovery_cache,
    documents,
    provider_daily_usage,
    sources,
)


async def main() -> None:
    db = create_database(get_settings().database_url)
    try:
        async with db.sessions() as session:
            by_status = (
                await session.execute(
                    sa.select(documents.c.retrieval_status, sa.func.count())
                    .group_by(documents.c.retrieval_status)
                )
            ).all()
            approved = await session.scalar(
                sa.select(sa.func.count()).select_from(sources).where(
                    sources.c.enabled.is_(True),
                    sources.c.approved_at.is_not(None),
                    sources.c.license_label.is_not(None),
                    sources.c.license_label != "",
                )
            )
            cache_entries = await session.scalar(
                sa.select(sa.func.count()).select_from(discovery_cache)
                .where(discovery_cache.c.expires_at > sa.func.now())
            )
            licensed_rows = (await session.execute(
                sa.select(sources.c.domain, documents.c.retrieval_status)
                .join(documents, documents.c.source_id == sources.c.id)
                .where(
                    sources.c.enabled.is_(True),
                    sources.c.approved_at.is_not(None),
                    sources.c.license_label.is_not(None),
                    sources.c.license_label != "",
                    documents.c.retrieval_status.in_(("ready", "text_only")),
                )
            )).all()
            indexed_by_category = Counter(
                classify_source("https://" + row.domain).source_category
                for row in licensed_rows
            )
            credits = await session.scalar(
                sa.select(provider_daily_usage.c.credits_used).where(
                    provider_daily_usage.c.provider == "newsdata",
                    provider_daily_usage.c.day_utc == datetime.now(UTC).date(),
                )
            )
        print(json.dumps({
            "documents_by_status": dict(by_status),
            "approved_publishers": approved,
            "citable_documents_by_category": {
                category: indexed_by_category.get(category, 0)
                for category in ("official", "major_media", "fact_check", "other_media")
            },
            "active_discovery_cache_entries": cache_entries,
            "newsdata_credits_reserved_today": credits or 0,
        }, ensure_ascii=False, indent=2))
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
