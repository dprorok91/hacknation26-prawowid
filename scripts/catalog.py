"""Import metadata-only corpus records or explicitly approve a publisher's text policy."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from prawdowid.config import get_settings
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import ArticleSnapshot, store_article
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import documents, sources

KINDS = ("primary", "news", "factcheck")


def catalog_records(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("metadata_only") is not True or entry.get("source_type") not in KINDS:
            raise ValueError("Import accepts metadata-only records with known source types.")
        url = canonical_url(entry["url"])
        domain = urlsplit(url).hostname
        if (
            urlsplit(url).scheme != "https"
            or not domain
            or domain.removeprefix("www.") != entry["domain"].removeprefix("www.")
        ):
            raise ValueError("Use canonical HTTPS links for their declared domains.")
        collected = datetime.fromisoformat(entry["retrieved_at"])
        if collected.tzinfo is None:
            raise ValueError("Retrieval timestamps must have a time zone.")
        published = entry.get("published_at")
        published = datetime.fromisoformat(published) if published and "T" in published else None
        if published and published.tzinfo is None:
            published = None
        yield entry, ArticleSnapshot(url, entry["publisher"], entry["title"], published, collected)


async def import_catalog(database, path):
    records = list(catalog_records(path))
    imported = 0
    # Each write is a short, repeatable transaction. Imported catalog data never approves text.
    for entry, snap in records:
        async with database.sessions.begin() as session:
            await session.execute(
                insert(sources)
                .values(
                    domain=urlsplit(snap.url).hostname,
                    display_name=snap.publisher,
                    source_type=entry["source_type"],
                    policy_url=entry["policy_url"],
                    license_label=entry["policy_label"],
                )
                .on_conflict_do_nothing(index_elements=[sources.c.domain])
            )
            active = await session.scalar(
                select(documents.c.id).where(documents.c.canonical_url == snap.url)
            )
            if active is None:
                await store_article(session, snap)
                imported += 1
    print(f"Imported {imported} metadata-only records ({len(records)} reviewed links).")


async def approve(database, entry):
    policy = canonical_url(entry.policy)
    domain = urlsplit(canonical_url("https://" + entry.domain)).hostname
    if urlsplit(policy).scheme != "https" or entry.domain != domain or not entry.license.strip():
        raise ValueError("Provide an exact publisher hostname and a verified HTTPS license policy.")
    values = {
        "display_name": entry.publisher,
        "source_type": entry.type,
        "policy_url": policy,
        "license_label": entry.license,
        "enabled": True,
        "approved_at": datetime.now(UTC),
    }
    async with database.sessions.begin() as session:
        await session.execute(
            insert(sources)
            .values(domain=domain, **values)
            .on_conflict_do_update(index_elements=[sources.c.domain], set_=values)
        )
    print(f"Approved text policy for {domain}.")


async def run(args):
    database = create_database(get_settings().database_url)
    try:
        if args.command == "import":
            await import_catalog(database, args.records)
        else:
            await approve(database, args)
    finally:
        await database.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import")
    load.add_argument("records", type=Path)
    approval = commands.add_parser("approve")
    approval.add_argument("domain")
    approval.add_argument("--publisher", required=True)
    approval.add_argument("--type", choices=KINDS, required=True)
    approval.add_argument("--license", required=True)
    approval.add_argument("--policy", required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
