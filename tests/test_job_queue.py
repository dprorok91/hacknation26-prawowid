import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from prawdowid.application.worker import JobFailure, JobLease, JobWorker
from prawdowid.infrastructure.db_models import audit_events, jobs
from prawdowid.infrastructure.job_queue import PostgresJobQueue, insert_job

ROOT = Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture
async def sessions():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run the real PostgreSQL queue checks.")
    schema = f"prawdowid_queue_{uuid4().hex}"
    admin = create_async_engine(url)
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": f'"{schema}", public'}}
    )

    def migrate(sync_connection):
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = sync_connection
        config.attributes["version_table_schema"] = schema
        command.upgrade(config, "head")

    try:
        async with admin.begin() as conn:
            await conn.execute(CreateSchema(schema))
        async with engine.begin() as conn:
            await conn.run_sync(migrate)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        try:
            async with admin.begin() as conn:
                await conn.execute(DropSchema(schema, cascade=True, if_exists=True))
        finally:
            await admin.dispose()


@pytest.mark.asyncio
async def test_concurrent_enqueue_and_two_distinct_leases(sessions):
    queue = PostgresJobQueue(sessions)
    job_ids = await asyncio.gather(
        *(
            queue.enqueue(kind="fetch", dedupe_key="same-url", payload={"n": idx})
            for idx in range(40)
        )
    )
    assert len(set(job_ids)) == 1

    second = await queue.enqueue(kind="fetch", dedupe_key="other-url", payload={})
    leases = await asyncio.gather(queue.lease_next("one"), queue.lease_next("two"))
    assert all(leases)
    assert {lease.id for lease in leases} == {job_ids[0], second}
    assert await queue.lease_next("third") is None

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(jobs)) == 2


@pytest.mark.asyncio
async def test_insert_job_respects_caller_transaction(sessions):
    queue = PostgresJobQueue(sessions)
    async with sessions() as session:
        await insert_job(session, kind="fetch", dedupe_key="uncommitted", payload={})
        assert await queue.lease_next("other") is None
        await session.rollback()
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(jobs)) == 0


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_fenced_even_for_same_worker(sessions):
    async def on_dead(session, lease, error_code):
        await session.execute(insert(audit_events).values(event_type=f"{lease.kind}:{error_code}"))

    queue = PostgresJobQueue(sessions, max_attempts=2, on_dead=on_dead)
    job_id = await queue.enqueue(kind="fetch", dedupe_key="same-url", payload={})
    first = await queue.lease_next("worker")
    assert first.id == job_id and first.attempt == 1
    assert await queue.heartbeat(first)

    async def expire():
        async with sessions.begin() as session:
            await session.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(lease_until=text("clock_timestamp() - interval '1 second'"))
            )

    await expire()
    assert not await queue.complete(first)
    second = await queue.lease_next("worker")
    assert second.id == first.id and second.attempt == 2
    assert not await queue.heartbeat(first)
    assert not await queue.complete(first)
    assert not await queue.fail(first, "timeout", retryable=True)

    await expire()
    assert await queue.lease_next("other") is None
    assert await queue.lease_next("other") is None
    async with sessions() as session:
        state = (
            await session.execute(
                select(jobs.c.state, jobs.c.error_code).where(jobs.c.id == job_id)
            )
        ).one()
        assert state == ("dead", "timeout")
        assert (await session.scalars(select(audit_events.c.event_type))).all() == ["fetch:timeout"]


@pytest.mark.asyncio
async def test_completion_and_effect_rollback_together(sessions):
    queue = PostgresJobQueue(sessions)
    await queue.enqueue(kind="fetch", dedupe_key="url", payload={})
    lease = await queue.lease_next("worker")

    async def broken_effect(session):
        await session.execute(insert(audit_events).values(event_type="temporary"))
        raise RuntimeError("effect failed")

    with pytest.raises(RuntimeError, match="effect failed"):
        await queue.complete(lease, broken_effect)

    async def effect(session):
        await session.execute(insert(audit_events).values(event_type="committed"))

    assert await queue.complete(lease, effect)
    assert not await queue.complete(lease, effect)
    async with sessions() as session:
        assert await session.scalar(select(jobs.c.state)) == "completed"
        assert (await session.scalars(select(audit_events.c.event_type))).all() == ["committed"]


@pytest.mark.asyncio
async def test_retry_waits_and_terminal_failure_triggers_hook(sessions):
    async def on_dead(session, lease, error_code):
        await session.execute(insert(audit_events).values(event_type=error_code))

    queue = PostgresJobQueue(sessions, retry_base_seconds=60, max_attempts=2, on_dead=on_dead)
    job_id = await queue.enqueue(kind="fetch", dedupe_key="url", payload={})
    lease = await queue.lease_next("worker")
    assert await queue.fail(lease, "source_unavailable", retryable=True)
    assert await queue.lease_next("worker") is None
    async with sessions.begin() as session:
        await session.execute(
            update(jobs).where(jobs.c.id == job_id).values(run_after=func.clock_timestamp())
        )
    second = await queue.lease_next("worker")
    assert second.attempt == 2
    assert await queue.fail(second, "timeout", retryable=True)
    async with sessions() as session:
        assert await session.scalar(select(jobs.c.state)) == "dead"
        assert (await session.scalars(select(audit_events.c.event_type))).all() == ["timeout"]


class MemoryQueue:
    def __init__(self):
        self.available = True
        self.events = []

    async def lease_next(self, worker_id):
        if not self.available:
            return None
        self.available = False
        return JobLease("job", "fetch", "key", 0, {}, worker_id, 1, datetime.now(UTC))

    async def heartbeat(self, lease):
        self.events.append("heartbeat")
        return True

    async def complete(self, lease, effect=None):
        self.events.append(effect)
        return True

    async def fail(self, lease, error_code, *, retryable):
        self.events.append((error_code, retryable))
        return True


@pytest.mark.asyncio
async def test_worker_completes_after_handler_and_stops_when_idle():
    queue = MemoryQueue()

    async def handle(lease):
        assert lease.worker_id == "worker"
        queue.events.append("handler")
        return "effect"

    worker = JobWorker(queue, "worker", handle, heartbeat_seconds=None)
    assert await worker.run_batch(2) == 1
    assert queue.events == ["handler", "effect"]


@pytest.mark.asyncio
async def test_worker_records_typed_failure():
    queue = MemoryQueue()

    async def handle(lease):
        raise JobFailure("provider_rate_limited", retryable=True)

    worker = JobWorker(queue, "worker", handle, heartbeat_seconds=None)
    assert await worker.run_once()
    assert queue.events == [("provider_rate_limited", True)]


@pytest.mark.asyncio
async def test_worker_cancels_handler_when_heartbeat_loses_lease():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class LostLeaseQueue(MemoryQueue):
        async def heartbeat(self, lease):
            await entered.wait()
            return False

    async def handle(lease):
        try:
            entered.set()
            await asyncio.Future()
        finally:
            cancelled.set()

    queue = LostLeaseQueue()
    worker = JobWorker(queue, "worker", handle, heartbeat_seconds=0.001)
    assert await asyncio.wait_for(worker.run_once(), timeout=1)
    assert cancelled.is_set()
    assert not queue.events
