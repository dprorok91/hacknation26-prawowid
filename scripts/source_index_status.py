"""Show approval and indexed-text status for every supplied official-source URL.

Usage: uv run --locked python scripts/source_index_status.py
Shows missing approvals without altering licensing, crawling or importing content.
"""

import asyncio
from collections import Counter
from urllib.parse import urlsplit

import sqlalchemy as sa

from prawdowid.config import get_settings
from prawdowid.infrastructure.auto_index import load_seeds
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import documents, sources


async def main() -> None:
    db = create_database(get_settings().database_url)
    try:
        async with db.sessions() as session:
            rows = (await session.execute(
                sa.select(
                    sources.c.domain,
                    sources.c.enabled,
                    sources.c.approved_at,
                    sources.c.license_label,
                )
            )).all()
            indexed = (await session.execute(
                sa.select(sources.c.domain, sa.func.count())
                .join(documents, documents.c.source_id == sources.c.id)
                .where(
                    documents.c.retrieval_status.in_(("ready", "text_only")),
                    documents.c.expires_at > sa.func.now(),
                    documents.c.text_license == sources.c.license_label,
                    sources.c.enabled.is_(True),
                    sources.c.approved_at.is_not(None),
                    sources.c.license_label.is_not(None),
                    sources.c.license_label != "",
                ).group_by(sources.c.domain)
            )).all()
        policies = {
            domain: bool(enabled and approved_at and label)
            for domain, enabled, approved_at, label in rows
        }
        counts = dict(indexed)
        summary: Counter[str] = Counter()
        for seed in load_seeds():
            host = urlsplit(seed.url).hostname or ""
            if not policies.get(host):
                status = "POLICY_NOT_APPROVED"
            elif counts.get(host, 0):
                status = "INDEXED"
            elif seed.mode not in ("html", "listing"):
                status = "NEEDS_SPECIAL_ADAPTER"
            else:
                status = "APPROVED_WAITING_FOR_TEXT"
            summary[status] += 1
            print(f"{status:27} {seed.url}")
        print("Summary (unique HTTP URLs):", dict(summary))
        print("INDEXED means at least one citable document on that host, not every URL.")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
