"""Boundedly discover HTML articles/PDF URLs on approved public listing pages.

Default: DRY RUN, no DB writes. `--apply` saves metadata and queues the
existing fetch worker. All pages are subject to robots/SSRF/size limits.
Uses the 40 listed entrypoints (or explicit --seed URLs) *only* where the
exact source domain has an existing active reuse approval.

  uv run --locked python scripts/discover_approved_articles.py
  uv run --locked python scripts/discover_approved_articles.py --seed https://example.gov.pl/aktualnosci --apply
"""

import argparse
import asyncio
import json
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import sqlalchemy as sa

from prawdowid.application.source_discovery import discover_links
from prawdowid.config import get_settings
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import approved_license, store_discovered_metadata
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import sources
from prawdowid.infrastructure.ingestion import enqueue_article
from prawdowid.infrastructure.safe_web import FetchError, SafeWeb

DEFAULT_SEEDS = Path(__file__).with_name("official_sources.json")


def load_seeds(explicit: list[str]) -> list[str]:
    raw = explicit or [row["url"] for row in json.loads(DEFAULT_SEEDS.read_text("utf-8"))]
    urls = []
    for supplied in raw:
        try:
            url = canonical_url(supplied)
        except ValueError:
            continue
        if url not in urls:
            urls.append(url)
    return urls


async def run(seeds: list[str], *, max_pages: int, max_links: int, apply: bool) -> None:
    db = create_database(get_settings().database_url)
    try:
        approved = set()
        async with db.sessions() as session:
            approved = set(await session.scalars(
                sa.select(sources.c.domain).where(
                    sources.c.enabled.is_(True),
                    sources.c.approved_at.is_not(None),
                    sources.c.license_label.is_not(None),
                    sources.c.license_label != "")
            ))
        allowed = [url for url in seeds if urlsplit(url).hostname in approved]
        print(f"Approved listing entrypoints: {len(allowed)} / {len(seeds)}")
        if not allowed:
            print("No approved listing domains; source registration does NOT grant content rights.")
            return
        queue = deque(allowed[:max_pages])
        seen = set()
        discoveries = {}
        async with httpx.AsyncClient(trust_env=False) as client:
            web = SafeWeb(client)
            while queue and len(seen) < max_pages and len(discoveries) < max_links:
                url = queue.popleft()
                if url in seen or urlsplit(url).hostname not in approved:
                    continue
                seen.add(url)
                try:
                    page = await web.read_article(url)
                except FetchError as exc:
                    print(f"SKIPPED {urlsplit(url).hostname}: {exc.reason}")
                    continue
                final_host = urlsplit(page.url).hostname
                if final_host not in approved or page.media_type not in (
                    "text/html", "application/xhtml+xml",
                ):
                    continue
                if "nofollow" in page.robots.casefold():
                    continue
                links = discover_links(page.content, page.url, max_links=max_links)
                for item in links.articles:
                    if len(discoveries) < max_links:
                        discoveries.setdefault(item.canonical_url, item)
                for next_page in links.next_pages:
                    if next_page not in seen and next_page not in queue:
                        queue.append(next_page)
        print(f"Visited pages: {len(seen)}; discovered article/PDF URLs: {len(discoveries)}")
        for candidate in discoveries.values():
            print(f"{candidate.canonical_url} — {candidate.headline[:100]}")
        if not apply:
            print("DRY RUN — no DB writes or article queue operations. Add --apply to enqueue.")
            return
        count = 0
        for candidate in discoveries.values():
            async with db.sessions.begin() as session:
                if not await approved_license(session, urlsplit(candidate.canonical_url).hostname):
                    continue
                await store_discovered_metadata(session, candidate)
                await enqueue_article(
                    session, candidate.canonical_url,
                    title=candidate.headline, publisher=candidate.publisher,
                )
                count += 1
        print(f"Queued up to {count} deduplicated approved-domain article jobs.")
    finally:
        await db.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="append", default=[], help="Approved listing page URL")
    parser.add_argument("--max-pages", type=int, default=8, help="Maximum HTML pages visited (1..30)")
    parser.add_argument("--max-links", type=int, default=25, help="Maximum new URLs queued (1..100)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 30 or not 1 <= args.max_links <= 100:
        parser.error("--max-pages must be 1..30 and --max-links must be 1..100")
    asyncio.run(run(
        load_seeds(args.seed), max_pages=args.max_pages,
        max_links=args.max_links, apply=args.apply,
    ))


if __name__ == "__main__":
    main()
