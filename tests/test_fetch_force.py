"""Explicit re-fetch can bypass cooldown but not duplicate a leased job."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prawdowid.infrastructure import ingestion


class Result:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class Session:
    def __init__(self, state):
        self.state = state

    async def execute(self, _statement):
        return Result(SimpleNamespace(
            id="old-job", generation=2, state=self.state, updated_at=datetime.now(UTC)
        ))


@pytest.mark.asyncio
async def test_explicit_force_starts_new_generation_for_completed_job(monkeypatch):
    seen = []

    async def insert_job(_session, **kwargs):
        seen.append(kwargs)
        return "new-job"

    monkeypatch.setattr(ingestion, "insert_job", insert_job)
    url = "https://www.bip.krakow.pl/?dok_id=226666"
    assert await ingestion.enqueue_article(Session("completed"), url) == "old-job"
    assert seen == []
    assert await ingestion.enqueue_article(Session("completed"), url, force=True) == "new-job"
    assert seen[0]["generation"] == 3
    assert seen[0]["payload"]["url"] == url


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["queued", "leased"])
async def test_force_does_not_duplicate_inflight_job(monkeypatch, state):
    async def should_not_enqueue(*args, **kwargs):
        pytest.fail("Should reuse in-flight job even with force=True")

    monkeypatch.setattr(ingestion, "insert_job", should_not_enqueue)
    assert await ingestion.enqueue_article(
        Session(state), "https://www.bip.krakow.pl/?dok_id=226666", force=True
    ) == "old-job"
