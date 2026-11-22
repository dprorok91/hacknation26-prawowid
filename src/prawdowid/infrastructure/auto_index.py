"""Bounded, periodic indexing of publisher-approved public documents.

Seed registration is metadata-only. Only explicitly approved domains can be
fetched or queued. Discovery links stay on the same approved domain, subject
to SafeWeb's robots, DNS, redirect and response-size controls.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import sqlalchemy as sa

from prawdowid.application.source_discovery import discover_links
from prawdowid.domain.models import DocumentCandidate
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.corpus import store_discovered_metadata
from prawdowid.infrastructure.db_models import documents, sources
from prawdowid.infrastructure.govpl_policy import GOVPL_HOST, approve_govpl
from prawdowid.infrastructure.ingestion import enqueue_article
from prawdowid.infrastructure.safe_web import FetchError

logger = logging.getLogger(__name__)
SEED_CATALOG = Path(__file__).resolve().parents[3] / "scripts" / "official_sources.json"
FETCHABLE_MODES = frozenset({"html", "listing"})
INTERVAL_SECONDS = 3600
MAX_DOCUMENTS_PER_CYCLE = 40
MAX_LISTINGS_PER_CYCLE = 8
MAX_DISCOVERED_PER_CYCLE = 40


@dataclass(frozen=True)
class IndexSeed:
    url: str
    headline: str
    publisher: str
    mode: str


def load_seeds(path: Path = SEED_CATALOG) -> tuple[IndexSeed, ...]:
    """Load reviewed entrypoints; never treat the list as a license grant."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("Source seed catalog must be a list")
    unique: dict[str, IndexSeed] = {}
    for item in raw:
        url = canonical_url(item["url"])
        if urlsplit(url).scheme != "https":
            raise ValueError("Only public HTTPS seed URLs are supported")
        unique.setdefault(
            url,
            IndexSeed(url, item["title"], item["publisher"], item["mode"]),
        )
    return tuple(unique.values())


def choose_documents(
    rows: list[tuple[str, str, str]],
    *,
    approved_domains: set[str],
    unsupported_seeds: set[str],
    limit: int,
) -> list[tuple[str, str, str]]:
    """Never schedule unapproved, interactive, or duplicate document URLs."""
    selected = []
    seen = set()
    for url, title, publisher in rows:
        if url in seen or url in unsupported_seeds:
            continue
        seen.add(url)
        if urlsplit(url).hostname not in approved_domains:
            continue
        selected.append((url, title, publisher))
        if len(selected) == limit:
            break
    return selected


def rotating_listing_seeds(
    seeds: tuple[IndexSeed, ...], approved_domains: set[str], *, hour: int
) -> tuple[IndexSeed, ...]:
    """Rotate work, rather than always fetching the first eight publishers."""
    eligible = [
        item for item in seeds
        if item.mode == "listing" and urlsplit(item.url).hostname in approved_domains
    ]
    if not eligible:
        return ()
    start = (hour * MAX_LISTINGS_PER_CYCLE) % len(eligible)
    return tuple((eligible * 2)[start:start + min(MAX_LISTINGS_PER_CYCLE, len(eligible))])


async def run_cycle(sessions, web, *, seeds: tuple[IndexSeed, ...] | None = None) -> dict:
    """Register all seeds; index/refresh only licensed domains; discover bounded links."""
    seeds = seeds if seeds is not None else load_seeds()
    # Source registration is metadata only and preserves pre-existing approvals.
    # New URLs never become evidence until the owner approves the source policy.
    async with sessions.begin() as session:
        for seed in seeds:
            await store_discovered_metadata(
                session,
                DocumentCandidate(
                    canonical_url=seed.url,
                    publisher=seed.publisher,
                    headline=seed.headline,
                    published_at=None,
                    provider="provided_seed_catalog",
                ),
            )
        if any(urlsplit(seed.url).hostname == GOVPL_HOST for seed in seeds):
            # The project explicitly enables this government host. Existing
            # per-source manual policies are preserved by the conditional upsert.
            await approve_govpl(session)

    async with sessions() as session:
        approved_domains = set(await session.scalars(
            sa.select(sources.c.domain).where(
                sources.c.enabled.is_(True),
                sources.c.approved_at.is_not(None),
                sources.c.license_label.is_not(None),
                sources.c.license_label != "",
            )
        ))
        rows = (await session.execute(
            sa.select(documents.c.canonical_url, documents.c.title, sources.c.display_name)
            .join(sources, documents.c.source_id == sources.c.id)
            .where(
                sources.c.domain.in_(approved_domains),
                sa.or_(
                    documents.c.retrieval_status == "metadata_only",
                    documents.c.expires_at.is_(None),
                    documents.c.expires_at <= sa.func.now(),
                ),
            )
            .order_by(documents.c.fetched_at.asc(), documents.c.canonical_url)
            .limit(MAX_DOCUMENTS_PER_CYCLE * 3)
        )).all() if approved_domains else []

    unsupported_seeds = {seed.url for seed in seeds if seed.mode not in FETCHABLE_MODES}
    candidates = choose_documents(
        rows, approved_domains=approved_domains,
        unsupported_seeds=unsupported_seeds, limit=MAX_DOCUMENTS_PER_CYCLE,
    )
    for url, title, publisher in candidates:
        async with sessions.begin() as session:
            await enqueue_article(session, url, title=title, publisher=publisher)

    # Listing pages are inspected only on already approved hosts, with SafeWeb's
    # bounded reads. Unknown hosts and offsite links are never scheduled.
    hour = int(datetime.now(UTC).timestamp()) // INTERVAL_SECONDS
    listings = rotating_listing_seeds(seeds, approved_domains, hour=hour)
    discovered = 0
    for seed in listings:
        if discovered >= MAX_DISCOVERED_PER_CYCLE:
            break
        try:
            page = await web.read_article(seed.url)
        except FetchError as exc:
            logger.info("Automatic source discovery skipped (%s): %s", urlsplit(seed.url).hostname, exc.reason)
            continue
        if urlsplit(page.url).hostname not in approved_domains:
            continue
        if page.media_type not in ("text/html", "application/xhtml+xml"):
            continue
        if "nofollow" in page.robots.casefold():
            continue
        links = discover_links(
            page.content, page.url,
            max_links=min(100, MAX_DISCOVERED_PER_CYCLE - discovered),
        )
        for item in links.articles:
            if discovered >= MAX_DISCOVERED_PER_CYCLE:
                break
            if urlsplit(item.canonical_url).hostname not in approved_domains:
                continue
            async with sessions.begin() as session:
                await store_discovered_metadata(session, item)
                await enqueue_article(
                    session, item.canonical_url,
                    title=item.headline, publisher=item.publisher,
                )
            discovered += 1
    summary = {
        "seeds": len(seeds),
        "approved_hosts": len(approved_domains),
        "scheduled_documents": len(candidates),
        "discovered_links": discovered,
    }
    logger.info("Automatic approved-source indexing cycle: %s", summary)
    return summary
