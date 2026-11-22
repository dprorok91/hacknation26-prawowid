"""Bounded, idempotent queueing of existing metadata from approved publishers.

Run without --apply first. The fetch worker performs HTTP, robots checks, and
license enforcement; this tool NEVER fetches or approves an external source.

Examples:
  uv run --locked python scripts/index_approved_sources.py --category official
  uv run --locked python scripts/index_approved_sources.py --category official --apply
  uv run --locked python scripts/index_approved_sources.py --category all --max-items 20 --apply
"""

import argparse
import asyncio
from collections import Counter
from urllib.parse import urlsplit

import sqlalchemy as sa

from prawdowid.config import get_settings
from prawdowid.domain.source_flags import classify_source
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import documents, sources
from prawdowid.infrastructure.ingestion import enqueue_article


def candidates_statement():
    """Only approved metadata; never refetch indexed text in this batch."""
    return (
        sa.select(documents.c.canonical_url, documents.c.title, sources.c.display_name)
        .join(sources, documents.c.source_id == sources.c.id)
        .where(
            documents.c.retrieval_status == "metadata_only",
            sources.c.enabled.is_(True),
            sources.c.approved_at.is_not(None),
            sources.c.license_label.is_not(None),
            sources.c.license_label != "",
        )
        .order_by(documents.c.fetched_at.desc(), documents.c.canonical_url)
    )


def choose_candidates(rows, *, category: str, max_items: int):
    """Filter by provenance and cap work *after* filtering, not before."""
    result = []
    seen = set()
    for url, title, publisher in rows:
        if url in seen:
            continue
        seen.add(url)
        flags = classify_source(url)
        if category != "all" and flags.source_category != category:
            continue
        result.append((url, title, publisher, flags.source_category))
        if len(result) >= max_items:
            break
    return result


async def run(*, category: str, max_items: int, apply: bool) -> None:
    db = create_database(get_settings().database_url)
    try:
        async with db.sessions() as session:
            rows = (await session.execute(candidates_statement())).all()
        eligible = choose_candidates(rows, category=category, max_items=max_items)
        print(f"Approved metadata candidates: {len(eligible)} / total metadata on approved domains: {len(rows)}")
        print("Selected categories:", dict(Counter(item[3] for item in eligible)))
        for url, _, _, label in eligible:
            print(f"{label}: {url}")
        if not apply:
            print("DRY RUN — no fetch jobs created; use --apply to enqueue.")
            return
        job_ids = set()
        # Reconfirm approval in the same transaction as enqueueing; the worker
        # separately checks the final URL after redirects.
        for url, title, publisher, _ in eligible:
            host = urlsplit(url).hostname
            async with db.sessions.begin() as session:
                still_approved = await session.scalar(
                    sa.select(sources.c.id).where(
                        sources.c.domain == host,
                        sources.c.enabled.is_(True),
                        sources.c.approved_at.is_not(None),
                        sources.c.license_label.is_not(None),
                        sources.c.license_label != "",
                    )
                )
                if still_approved is None:
                    continue
                job_ids.add(await enqueue_article(session, url, title=title, publisher=publisher))
        print(f"FETCH_URL jobs selected: {len(job_ids)} (may include deduplicated jobs)")
        print("Check actual indexed-text count using scripts/news_status.py after worker completes.")
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--category", choices=("official", "major_media", "fact_check", "other_media", "all"),
        default="official", help="Provenance category, default only approved government sources",
    )
    parser.add_argument("--max-items", type=int, default=20, help="Hard cap, 1 to 100 (default 20)")
    parser.add_argument("--apply", action="store_true", help="Queue worker jobs; otherwise preview")
    args = parser.parse_args()
    if not 1 <= args.max_items <= 100:
        parser.error("--max-items must be between 1 and 100")
    asyncio.run(run(category=args.category, max_items=args.max_items, apply=args.apply))


if __name__ == "__main__":
    main()
