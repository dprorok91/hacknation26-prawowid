"""Real PostgreSQL integration tests selected only with TEST_DATABASE_URL."""

import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa

from prawdowid.application.analyses import Submission
from prawdowid.application.worker import JobWorker
from prawdowid.infrastructure.analysis_store import (
    PostgresAnalyses,
    claim_analysis_input,
    finish_analysis,
)
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import analyses, jobs, sources
from prawdowid.infrastructure.job_queue import PostgresJobQueue
from prawdowid.main import create_app


@pytest.mark.asyncio
async def test_abandoned_lease_is_recovered_after_a_worker_process_dies():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")
    job_kind = "PROCESS_RESTART_" + uuid4().hex
    database = create_database(url)
    queue = PostgresJobQueue(database.sessions, lease_seconds=0.75, kinds=(job_kind,))
    job_id = await queue.enqueue(kind=job_kind, dedupe_key="restart", payload={})
    worker = """
import asyncio
import os
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.job_queue import PostgresJobQueue
async def run():
    database = create_database(os.environ["TEST_DATABASE_URL"])
    queue = PostgresJobQueue(database.sessions, lease_seconds=0.75, kinds=[os.environ["TEST_JOB_KIND"]])
    lease = await queue.lease_next("original-process")
    print(lease.id if lease else "", flush=True)
    await asyncio.Event().wait()
asyncio.run(run())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        worker,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "TEST_JOB_KIND": job_kind},
    )
    try:
        output = await asyncio.wait_for(process.stdout.readline(), timeout=4)
        assert output.decode().strip() == job_id
        process.kill()
        await process.wait()

        async with asyncio.timeout(5):
            while (lease := await queue.lease_next("restarted-process")) is None:
                await asyncio.sleep(0.1)
        assert lease.id == job_id and lease.attempt == 2
        assert await queue.complete(lease)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        async with database.sessions.begin() as session:
            await session.execute(sa.delete(jobs).where(jobs.c.id == job_id))
        await database.dispose()


@pytest.mark.asyncio
async def test_40_successful_posts_and_two_overlapping_workers():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")
    token = uuid4().hex
    domain = f"{token}.example"
    resource = f"https://{domain}/{token}"
    database = create_database(url)
    service = PostgresAnalyses(database.sessions)
    queue = PostgresJobQueue(database.sessions, kinds=("ANALYZE",))
    fetch_keys = {sha256((resource + suffix).encode()).hexdigest() for suffix in ("", "/restart")}
    first_started, second_started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observations = {}

    async def handle(lease):
        analysis_id = lease.payload["analysis_id"]
        assert analysis_id not in observations
        async with database.sessions.begin() as session:
            claimed = await claim_analysis_input(session, lease)
        assert claimed.public_model_allowed is (analysis_id == main_id)
        observations[analysis_id] = lease.worker_id
        if lease.worker_id == "worker-1":
            first_started.set()
        else:
            second_started.set()
        await finish.wait()

        async def effect(session):
            await finish_analysis(
                session,
                analysis_id,
                report={"analysis_id": analysis_id, "source_snapshot": "fixture"},
                model_name="model-fixture",
                estimated_cost_usd=Decimal("0.0056") if claimed.public_model_allowed else None,
            )

        return effect

    transport = httpx.ASGITransport(app=create_app(service))
    analysis_ids = set()
    try:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.insert(sources).values(
                    domain=domain,
                    display_name="Approved fixture",
                    source_type="primary",
                    enabled=True,
                    approved_at=datetime.now(UTC),
                    license_label="fixture",
                )
            )
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            *submissions, another = await asyncio.gather(
                *[
                    client.post("/api/v1/analyses", json={"kind": "url", "content": resource})
                    for _ in range(40)
                ],
                client.post(
                    "/api/v1/adapters/message-demo", json={"kind": "url", "content": resource}
                ),
            )
            assert all(response.status_code == 202 for response in submissions)
            analysis_ids.update(response.json()["analysis_id"] for response in submissions)
            assert len(analysis_ids) == 1
            main_id = next(iter(analysis_ids))
            assert another.status_code == 202
            assert another.json()["analysis_id"] != main_id
            analysis_ids.add(another.json()["analysis_id"])

            first = JobWorker(queue, "worker-1", handle)
            second = JobWorker(queue, "worker-2", handle)
            async with asyncio.timeout(10), asyncio.TaskGroup() as group:
                group.create_task(first.run_once())
                await first_started.wait()
                group.create_task(second.run_once())
                await second_started.wait()
                finish.set()

            assert set(observations) == analysis_ids
            assert set(observations.values()) == {"worker-1", "worker-2"}
            response = await client.get(f"/api/v1/analyses/{main_id}")
            assert response.json()["status"] == "completed"
            assert response.json()["report"]["source_snapshot"] == "fixture"
            assert (await service.audit(main_id)).estimated_cost_usd == 0.0056
            assert (await service.audit(another.json()["analysis_id"])).estimated_cost_usd is None
            async with database.sessions() as session:
                assert (
                    await session.scalar(
                        sa.select(sa.func.count())
                        .select_from(jobs)
                        .where(jobs.c.kind == "ANALYZE", jobs.c.dedupe_key.in_(analysis_ids))
                    )
                    == 2
                )

            restart_body = {"kind": "url", "content": resource + "/restart"}
            expired_id = (await client.post("/api/v1/analyses", json=restart_body)).json()[
                "analysis_id"
            ]
            analysis_ids.add(expired_id)
            old_lease = await queue.lease_next("expired-worker")
            assert old_lease is not None
            async with database.sessions.begin() as session:
                await session.execute(
                    sa.update(jobs)
                    .where(jobs.c.id == old_lease.id)
                    .values(lease_until=sa.func.now())
                )
                await session.execute(
                    sa.update(analyses)
                    .where(analyses.c.id == expired_id)
                    .values(expires_at=sa.func.now())
                )
                assert await claim_analysis_input(session, old_lease) is None
            replacement = await client.post("/api/v1/analyses", json=restart_body)
            new_id = replacement.json()["analysis_id"]
            analysis_ids.add(new_id)
            assert new_id != expired_id
            assert (await client.get(f"/api/v1/analyses/{new_id}")).status_code == 200
    finally:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.delete(jobs).where(jobs.c.dedupe_key.in_(analysis_ids | fetch_keys))
            )
            await session.execute(sa.delete(analyses).where(analyses.c.id.in_(analysis_ids)))
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["legacy", "revoked", "late_approval"])
async def test_claim_requires_persisted_eligibility_and_current_source_approval(scenario):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")
    database = create_database(url)
    domain = f"{uuid4().hex}.example"
    resource = f"https://{domain}/report"
    service = PostgresAnalyses(database.sessions)
    queue = PostgresJobQueue(database.sessions, kinds=("ANALYZE",))
    submission = None
    try:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.insert(sources).values(
                    domain=domain,
                    display_name="Fixture",
                    source_type="primary",
                    approved_at=datetime.now(UTC),
                    enabled=scenario != "late_approval",
                    license_label="fixture",
                )
            )
        submission = await service.submit(Submission("url", resource, public_model_allowed=True))
        async with database.sessions.begin() as session:
            if scenario == "legacy":
                await session.execute(
                    sa.update(jobs)
                    .where(jobs.c.kind == "ANALYZE", jobs.c.dedupe_key == submission.analysis_id)
                    .values(payload_json={"analysis_id": submission.analysis_id})
                )
            else:
                await session.execute(
                    sa.update(sources)
                    .where(sources.c.domain == domain)
                    .values(enabled=scenario == "late_approval")
                )
        lease = await queue.lease_next("eligibility-test")
        assert lease is not None and lease.payload["analysis_id"] == submission.analysis_id
        async with database.sessions.begin() as session:
            claimed = await claim_analysis_input(session, lease)
            assert claimed is not None and claimed.public_model_allowed is False
    finally:
        if submission is not None:
            await service.delete(submission.analysis_id, submission.delete_token)
        async with database.sessions.begin() as session:
            await session.execute(
                sa.delete(jobs).where(
                    jobs.c.kind == "FETCH_URL",
                    jobs.c.dedupe_key == sha256(resource.encode()).hexdigest(),
                )
            )
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()
