"""Transactional PostgreSQL lease and retry operations."""

from collections.abc import Awaitable, Callable, Collection, Mapping
from datetime import datetime, timedelta
from math import isfinite
from random import uniform
from typing import Any

from sqlalchemy import Interval, and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prawdowid.application.worker import JobLease
from prawdowid.infrastructure.db_models import jobs

CompletionEffect = Callable[[AsyncSession], Awaitable[None]]
DeadJobEffect = Callable[[AsyncSession, JobLease, str], Awaitable[None]]
EXPIRED_BATCH_SIZE = 32
MAX_RETRY_DELAY = 300


async def insert_job(
    session: AsyncSession,
    *,
    kind: str,
    dedupe_key: str,
    payload: dict[str, Any],
    generation: int = 0,
    priority: int = 100,
    run_after: datetime | None = None,
) -> str:
    values = {
        "kind": kind,
        "dedupe_key": dedupe_key,
        "payload_json": payload,
        "generation": generation,
        "priority": priority,
    }
    if run_after is not None:
        values["run_after"] = run_after
    key = (jobs.c.kind, jobs.c.dedupe_key, jobs.c.generation)
    statement = (
        insert(jobs)
        .values(**values)
        .on_conflict_do_nothing(index_elements=key)
        .returning(jobs.c.id)
    )
    job_id = await session.scalar(statement)
    if job_id is not None:
        return job_id
    # A separate statement in READ COMMITTED sees the row that won the unique-key race.
    existing = await session.execute(
        select(jobs.c.id).where(
            jobs.c.kind == kind, jobs.c.dedupe_key == dedupe_key, jobs.c.generation == generation
        )
    )
    return existing.scalar_one()


class PostgresJobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: float = 30,
        max_attempts: int = 3,
        retry_base_seconds: float = 1,
        kinds: Collection[str] | None = None,
        on_dead: DeadJobEffect | None = None,
    ) -> None:
        if not isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not isfinite(retry_base_seconds) or retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be finite and positive")
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.kinds = tuple(kinds) if kinds is not None else None
        self.on_dead = on_dead

    async def enqueue(
        self,
        *,
        kind: str,
        dedupe_key: str,
        payload: dict[str, Any],
        generation: int = 0,
        priority: int = 100,
        run_after: datetime | None = None,
    ) -> str:
        async with self.sessions.begin() as session:
            return await insert_job(
                session,
                kind=kind,
                dedupe_key=dedupe_key,
                payload=payload,
                generation=generation,
                priority=priority,
                run_after=run_after,
            )

    async def lease_next(self, worker_id: str) -> JobLease | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        available = or_(
            and_(jobs.c.state == "queued", jobs.c.run_after <= func.clock_timestamp()),
            _expired_lease(),
        )
        candidate = (
            select(jobs.c.id)
            .where(self._kind_filter(), jobs.c.attempts < self.max_attempts, available)
            .order_by(jobs.c.priority, jobs.c.run_after, jobs.c.created_at, jobs.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .cte("job_to_lease")
        )
        async with self.sessions.begin() as session:
            await self._reap_expired(session)
            result = await session.execute(
                update(jobs)
                .where(jobs.c.id == candidate.c.id)
                .values(
                    state="leased",
                    lease_owner=worker_id,
                    lease_until=_clock_plus(self.lease_seconds),
                    attempts=jobs.c.attempts + 1,
                    updated_at=func.clock_timestamp(),
                )
                .returning(*jobs.c)
            )
            row = result.mappings().one_or_none()
            return _lease(row) if row is not None else None

    async def heartbeat(self, lease: JobLease) -> bool:
        async with self.sessions.begin() as session:
            job_id = await session.scalar(
                update(jobs)
                .where(_active_lease(lease))
                .values(
                    lease_until=func.greatest(jobs.c.lease_until, _clock_plus(self.lease_seconds)),
                    updated_at=func.clock_timestamp(),
                )
                .returning(jobs.c.id)
            )
            return job_id is not None

    async def complete(self, lease: JobLease, effect: CompletionEffect | None = None) -> bool:
        async with self.sessions.begin() as session:
            job_id = await session.scalar(
                update(jobs)
                .where(_active_lease(lease))
                .values(
                    state="completed",
                    lease_owner=None,
                    lease_until=None,
                    error_code=None,
                    updated_at=func.clock_timestamp(),
                )
                .returning(jobs.c.id)
            )
            if job_id is None:
                return False
            if effect is not None:
                await effect(session)
            return True

    async def fail(self, lease: JobLease, error_code: str, *, retryable: bool) -> bool:
        terminal = not retryable or lease.attempt >= self.max_attempts
        backoff = self.retry_base_seconds * 2 ** min(lease.attempt - 1, 20)
        delay = min(MAX_RETRY_DELAY, backoff * uniform(0.75, 1.25))
        async with self.sessions.begin() as session:
            job_id = await session.scalar(
                update(jobs)
                .where(_active_lease(lease))
                .values(
                    state="dead" if terminal else "queued",
                    lease_owner=None,
                    lease_until=None,
                    run_after=jobs.c.run_after if terminal else _clock_plus(delay),
                    error_code=error_code,
                    updated_at=func.clock_timestamp(),
                )
                .returning(jobs.c.id)
            )
            if job_id is None:
                return False
            if terminal and self.on_dead is not None:
                await self.on_dead(session, lease, error_code)
            return True

    def _kind_filter(self):
        return jobs.c.kind.in_(self.kinds) if self.kinds is not None else True

    async def _reap_expired(self, session: AsyncSession) -> None:
        expired = await session.execute(
            select(jobs)
            .where(self._kind_filter(), jobs.c.attempts >= self.max_attempts, _expired_lease())
            .order_by(jobs.c.lease_until, jobs.c.id)
            .limit(EXPIRED_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        for row in expired.mappings():
            lease = _lease(row)
            await session.execute(
                update(jobs)
                .where(jobs.c.id == lease.id)
                .values(
                    state="dead",
                    lease_owner=None,
                    lease_until=None,
                    error_code="timeout",
                    updated_at=func.clock_timestamp(),
                )
            )
            if self.on_dead is not None:
                await self.on_dead(session, lease, "timeout")


def _clock_plus(seconds: float):
    return func.clock_timestamp() + literal(timedelta(seconds=seconds), type_=Interval())


def _expired_lease():
    return and_(jobs.c.state == "leased", jobs.c.lease_until <= func.clock_timestamp())


def _active_lease(lease: JobLease):
    return and_(
        jobs.c.id == lease.id,
        jobs.c.state == "leased",
        jobs.c.lease_owner == lease.worker_id,
        jobs.c.attempts == lease.attempt,
        jobs.c.lease_until > func.clock_timestamp(),
    )


def _lease(row: Mapping[str, Any]) -> JobLease:
    return JobLease(
        id=row["id"],
        kind=row["kind"],
        dedupe_key=row["dedupe_key"],
        generation=row["generation"],
        payload=row["payload_json"],
        worker_id=row["lease_owner"],
        attempt=row["attempts"],
        lease_until=row["lease_until"],
    )
