"""Register reviewed official URLs as metadata ONLY; never approve fetching rights.

Usage:
  uv run --locked python scripts/register_official_sources.py              # preview
  uv run --locked python scripts/register_official_sources.py --apply      # DB upsert

A hash route (``#/...``) is a browser state, not a separate HTTP resource.
This import intentionally does not crawl content or authorize text extraction.
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert

from prawdowid.config import get_settings
from prawdowid.domain.urls import canonical_url

RECORDS = Path(__file__).with_name("official_sources.json")


def official_seeds(path: Path = RECORDS) -> list[dict[str, str]]:
    """Validate each supplied URL and keep original hash routes in the manifest."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Expected a nonempty list of official sources")
    output = []
    for row in rows:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(key), str) and row[key].strip()
            for key in ("url", "title", "publisher", "mode")
        ):
            raise ValueError("Invalid source record")
        if row["mode"] not in {"html", "listing", "interactive", "client_route", "data_portal"}:
            raise ValueError("Unsupported source mode")
        canonical = canonical_url(row["url"])
        if urlsplit(canonical).scheme != "https":
            raise ValueError("Only safe, public HTTPS source URLs are accepted")
        output.append({**row, "canonical": canonical, "domain": urlsplit(canonical).hostname})
    return output


async def register(db, entries: list[dict[str, str]]) -> tuple[int, int]:
    from prawdowid.infrastructure.db_models import document_versions, documents, sources

    inserted = 0
    domains_added = 0
    # Idempotent inserts. Existing approved source policy and indexed documents
    # must NEVER be overwritten or downgraded to metadata-only.
    for item in entries:
        async with db.sessions.begin() as session:
            source_result = await session.scalar(
                insert(sources)
                .values(
                    domain=item["domain"],
                    display_name=item["publisher"],
                    source_type="primary",
                    enabled=False,
                )
                .on_conflict_do_nothing(index_elements=[sources.c.domain])
                .returning(sources.c.id)
            )
            domains_added += source_result is not None
            source_id = source_result or await session.scalar(
                sa.select(sources.c.id).where(sources.c.domain == item["domain"])
            )
            document_id = await session.scalar(
                insert(documents)
                .values(
                    source_id=source_id,
                    canonical_url=item["canonical"],
                    url_hash=sha256(item["canonical"].encode()).hexdigest(),
                    title=item["title"],
                    fetched_at=datetime.now(UTC),
                    retrieval_status="metadata_only",
                )
                .on_conflict_do_nothing(index_elements=[documents.c.canonical_url])
                .returning(documents.c.id)
            )
            if document_id:
                await session.execute(
                    sa.insert(document_versions).values(
                        document_id=document_id,
                        content_version=1,
                        title=item["title"],
                        fetched_at=datetime.now(UTC),
                        retrieval_status="metadata_only",
                    )
                )
                inserted += 1
    return inserted, domains_added


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Import metadata into the local DB")
    args = parser.parse_args()
    entries = official_seeds()
    unique = len({entry["canonical"] for entry in entries})
    modes = dict(sorted(Counter(entry["mode"] for entry in entries).items()))
    print(f"Listed URLs: {len(entries)} | HTTP resources: {unique} | Modes: {modes}")
    if not args.apply:
        print("Preview only. Use --apply after installing V10. No URLs were fetched or approved.")
        return

    async def run():
        from prawdowid.infrastructure.db import create_database

        db = create_database(get_settings().database_url)
        try:
            return await register(db, entries)
        finally:
            await db.dispose()

    inserted, domains_added = asyncio.run(run())
    print(f"DB: new metadata-only documents={inserted}; new domains={domains_added}.")
    print("Source approval is unchanged. No article fetch was queued.")


if __name__ == "__main__":
    main()
