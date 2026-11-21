"""Async PostgreSQL session lifecycle."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True)
class Database:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database(url: str) -> Database:
    engine = create_async_engine(
        url, pool_pre_ping=True, pool_size=5, max_overflow=5, pool_timeout=5, hide_parameters=True
    )
    return Database(engine, async_sessionmaker(engine, expire_on_commit=False))
