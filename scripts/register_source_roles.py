"""Register official / major-media / fact-check publishers as metadata only.

It is safe to run repeatedly. Does not authorize fetching, change licensing,
or create evidence passages. Existing publishers are assigned only the
legacy DB `source_type`; richer labels are calculated in reports by host.

  uv run --locked python scripts/register_source_roles.py
  uv run --locked python scripts/register_source_roles.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from prawdowid.config import get_settings
from prawdowid.domain.source_flags import (
    FACT_CHECK_DOMAINS,
    MAJOR_MEDIA_DOMAINS,
    OFFICIAL_DOMAINS,
    classify_source,
    legacy_source_type,
)
from prawdowid.infrastructure.db_models import sources

SEEDED_DOMAINS = sorted(OFFICIAL_DOMAINS | FACT_CHECK_DOMAINS | MAJOR_MEDIA_DOMAINS)


async def sync_roles(db) -> dict[str, int]:
    counters: Counter[str] = Counter()
    async with db.sessions.begin() as session:
        existing = (await session.execute(
            sa.select(sources.c.domain, sources.c.id, sources.c.source_type)
        )).all()
        # Normalize already-registered publishers. Approval and license
        # status are never changed, and names are preserved.
        for domain, source_id, current in existing:
            flags = classify_source("https://" + domain + "/")
            if flags.classification_basis == "unclassified":
                continue
            target = legacy_source_type(flags)
            if current != target:
                await session.execute(
                    sa.update(sources)
                    .where(sources.c.id == source_id)
                    .values(source_type=target)
                )
                counters["updated"] += 1
        for domain in SEEDED_DOMAINS:
            flags = classify_source("https://" + domain + "/")
            created = await session.scalar(
                insert(sources)
                .values(
                    domain=domain,
                    display_name=domain,
                    source_type=legacy_source_type(flags),
                    enabled=False,
                )
                .on_conflict_do_nothing(index_elements=[sources.c.domain])
                .returning(sources.c.id)
            )
            if created is not None:
                counters[flags.source_category] += 1
    return dict(counters)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    counts = Counter(
        classify_source("https://" + domain + "/").source_category
        for domain in SEEDED_DOMAINS
    )
    print(f"Registered domain examples: {len(SEEDED_DOMAINS)}; types: {dict(counts)}")
    if not args.apply:
        print("Preview only. No DB changes, approvals or fetch jobs.")
        return

    async def run() -> dict[str, int]:
        from prawdowid.infrastructure.db import create_database

        db = create_database(get_settings().database_url)
        try:
            return await sync_roles(db)
        finally:
            await db.dispose()

    print("Database changes:", asyncio.run(run()))
    print("Existing licensing and publisher approval are unchanged.")


if __name__ == "__main__":
    main()
