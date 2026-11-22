"""Queue explicitly approved public article URLs for the existing fetch worker.

Usage: uv run --locked python scripts/queue_approved_urls.py https://example.org/article
No network call happens in this command; the running worker fetches approved URLs.
"""

import argparse
import asyncio
from urllib.parse import urlsplit

from prawdowid.config import get_settings
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import approved_license
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.ingestion import enqueue_article


async def enqueue(urls: list[str]) -> int:
    db = create_database(get_settings().database_url)
    queued = 0
    try:
        for supplied in urls:
            try:
                url = canonical_url(supplied)
            except ValueError:
                print("SKIPPED invalid HTTP(S) URL")
                continue
            domain = urlsplit(url).hostname
            async with db.sessions.begin() as session:
                permission = await approved_license(session, domain)
                if not permission:
                    print(f"SKIPPED {domain}: publisher approval is required")
                    continue
                job_id = await enqueue_article(session, url)
                print(f"QUEUED {url}: {job_id}")
                queued += 1
    finally:
        await db.dispose()
    return queued


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+", help="Public article URLs, one or more")
    args = parser.parse_args()
    completed = asyncio.run(enqueue(args.urls))
    if not completed:
        parser.exit(2, "No URLs queued. Check source approvals and URL validity.\n")


if __name__ == "__main__":
    main()
