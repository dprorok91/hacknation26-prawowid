"""PostgreSQL cache for public discovery metadata and optional API-credit budgets."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prawdowid.domain.models import EventQuery
from prawdowid.infrastructure.db_models import discovery_cache, provider_daily_usage
from prawdowid.infrastructure.discovery import DiscoveredArticle, cache_key


class PostgresDiscoveryCache:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, hours: int = 12):
        if not 1 <= hours <= 24:
            raise ValueError("Discovery TTL must be within 1..24 hours")
        self.sessions = sessions
        self.ttl = timedelta(hours=hours)

    async def get(self, provider: str, event: EventQuery) -> tuple[DiscoveredArticle, ...] | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    sa.select(discovery_cache.c.results_json).where(
                        discovery_cache.c.cache_key == cache_key(provider, event),
                        discovery_cache.c.expires_at > sa.func.now(),
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        items = row.results_json
        if not isinstance(items, list):
            return None
        try:
            return tuple(
                DiscoveredArticle(
                    canonical_url=item["url"],
                    publisher=item["publisher"],
                    headline=item["headline"],
                    published_at=datetime.fromisoformat(item["published_at"])
                    if item["published_at"] else None,
                    provider=provider,
                    domain=item["domain"],
                    seen_at=datetime.fromisoformat(item["seen_at"])
                    if item["seen_at"] else None,
                ) for item in items
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def put(self, provider: str, event: EventQuery, candidates) -> None:
        items = [
            {"url": item.canonical_url, "publisher": item.publisher,
             "headline": item.headline,
             "published_at": item.published_at.isoformat() if item.published_at else None,
             "seen_at": item.seen_at.isoformat() if item.seen_at else None,
             "domain": item.domain}
            for item in candidates[:30]
            if isinstance(item, DiscoveredArticle)
        ]
        expires = datetime.now(UTC) + self.ttl
        statement = insert(discovery_cache).values(
            cache_key=cache_key(provider, event), provider=provider,
            results_json=items, expires_at=expires,
        )
        async with self.sessions.begin() as session:
            await session.execute(statement.on_conflict_do_update(
                index_elements=[discovery_cache.c.cache_key],
                set_={"results_json": items, "expires_at": expires,
                      "created_at": sa.func.now()},
            ))


class PostgresDailyBudget:
    """Atomic credit reservation across processes; conservative on network failures."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, limit: int):
        if not 1 <= limit <= 200:
            raise ValueError("Daily budget must be between 1 and 200")
        self.sessions = sessions
        self.limit = limit

    async def claim(self, provider: str, credits: int = 1) -> bool:
        if credits < 1 or credits > self.limit:
            return False
        day = datetime.now(UTC).date()
        statement = insert(provider_daily_usage).values(
            provider=provider, day_utc=day, credits_used=credits,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[provider_daily_usage.c.provider, provider_daily_usage.c.day_utc],
            set_={"credits_used": provider_daily_usage.c.credits_used + credits},
            where=provider_daily_usage.c.credits_used + credits <= self.limit,
        ).returning(provider_daily_usage.c.credits_used)
        async with self.sessions.begin() as session:
            value = await session.scalar(statement)
        return value is not None


async def prune_discovery_cache(session: AsyncSession) -> None:
    await session.execute(
        sa.delete(discovery_cache).where(discovery_cache.c.expires_at <= sa.func.now())
    )
    await session.execute(
        sa.delete(provider_daily_usage).where(
            provider_daily_usage.c.day_utc < datetime.now(UTC).date() - timedelta(days=7)
        )
    )
