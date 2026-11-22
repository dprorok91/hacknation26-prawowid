"""Safety and onboarding tests for the exact-host gov.pl approval policy."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from prawdowid.infrastructure import auto_index
from prawdowid.infrastructure.govpl_policy import (
    GOVPL_HOST,
    GOVPL_LICENSE_LABEL,
    GOVPL_POLICY_URL,
    govpl_approval_statement,
)


def test_govpl_approval_is_exact_host_only_and_records_provenance():
    stmt = govpl_approval_statement(approved_at=datetime(2026, 11, 22, tzinfo=UTC))
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    values = stmt.compile(dialect=postgresql.dialect()).params
    assert "ON CONFLICT" in sql
    assert "sources.approved_at IS NULL" in sql
    assert "sources.license_label IS NULL" in sql
    assert "sources.policy_url IS NULL" in sql
    assert "sources.enabled is false" in sql.lower()
    assert "Gov.pl copyright and reuse notice; first-page headline metadata" in values.values()
    assert "https://www.gov.pl/web/gov/prawa-autorskie" in values.values()
    assert GOVPL_HOST in values.values()
    assert GOVPL_LICENSE_LABEL in values.values()
    assert GOVPL_POLICY_URL in values.values()
    assert "ON CONFLICT" in sql and "enabled" in sql


@pytest.mark.asyncio
async def test_run_cycle_approves_only_the_explicit_government_host(monkeypatch):
    executed = []

    class FakeSession:
        async def execute(self, stmt):
            executed.append(stmt)
            return SimpleNamespace(all=list)

        async def scalars(self, stmt):
            return []

    class Context:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, *_args):
            return False

    class Sessions:
        def begin(self):
            return Context()

        def __call__(self):
            return Context()

    saved = AsyncMock()
    monkeypatch.setattr(auto_index, "store_discovered_metadata", saved)
    seeds = (
        auto_index.IndexSeed("https://www.gov.pl/web/rcb/raport", "RCB", "RCB", "html"),
        auto_index.IndexSeed("https://other.gov.pl/news", "Other", "Other", "html"),
    )
    result = await auto_index.run_cycle(Sessions(), SimpleNamespace(), seeds=seeds)
    assert result["seeds"] == 2
    assert saved.await_count == 2
    approval_sql = [
        str(stmt.compile(dialect=postgresql.dialect()))
        for stmt in executed if "ON CONFLICT" in str(stmt.compile(dialect=postgresql.dialect()))
    ]
    assert len(approval_sql) == 1
    assert "sources" in approval_sql[0]


@pytest.mark.asyncio
async def test_unrelated_hosts_never_trigger_autoapproval(monkeypatch):
    class Session:
        async def scalars(self, _stmt):
            return []

        async def execute(self, _stmt):
            return SimpleNamespace(all=list)

    class Context:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return False

    class Sessions:
        def begin(self):
            return Context()

        def __call__(self):
            return Context()

    approval = AsyncMock()
    monkeypatch.setattr(auto_index, "approve_govpl", approval)
    monkeypatch.setattr(auto_index, "store_discovered_metadata", AsyncMock())
    seed = auto_index.IndexSeed("https://fakegov.pl/news", "Fake", "Fake", "listing")
    await auto_index.run_cycle(Sessions(), SimpleNamespace(), seeds=(seed,))
    approval.assert_not_awaited()
