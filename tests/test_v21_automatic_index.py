"""Regression checks for periodic, policy-aware indexing of provided sources."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from prawdowid.infrastructure import auto_index


def _seed(url: str, mode: str) -> auto_index.IndexSeed:
    return auto_index.IndexSeed(url, url, "publisher", mode)


def test_catalog_loads_provided_sources_without_granting_approval(tmp_path: Path):
    entries = [
        {"url": "https://example.org/news", "title": "News", "publisher": "Test", "mode": "listing"},
        {"url": "https://example.org/news#/_/section", "title": "Route", "publisher": "Test", "mode": "client_route"},
    ]
    p = tmp_path / "sources.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    assert auto_index.load_seeds(p) == (
        auto_index.IndexSeed("https://example.org/news", "News", "Test", "listing"),
    )


def test_seed_catalog_nonlist_raises_typeerror(tmp_path: Path):
    p = tmp_path / "invalid-sources.json"
    p.write_text('{"url": "https://example.org"}', encoding="utf-8")
    with pytest.raises(TypeError, match="must be a list"):
        auto_index.load_seeds(p)


def test_choose_documents_filters_unapproved_and_unsupported_sources():
    rows = [
        ("https://bip.krakow.pl/a", "A", "BIP"),
        ("https://tvn24.pl/a", "B", "TVN"),
        ("https://bip.krakow.pl/map", "Interactive", "BIP"),
        ("https://bip.krakow.pl/a", "Duplicate", "BIP"),
        ("https://bip.krakow.pl/b", "C", "BIP"),
    ]
    assert auto_index.choose_documents(
        rows, approved_domains={"bip.krakow.pl"},
        unsupported_seeds={"https://bip.krakow.pl/map"}, limit=2,
    ) == [("https://bip.krakow.pl/a", "A", "BIP"), ("https://bip.krakow.pl/b", "C", "BIP")]


def test_listing_discovery_rotates_between_approved_publishers():
    seeds = tuple(_seed(f"https://example{i}.org/aktualnosci", "listing") for i in range(11))
    allowed = {f"example{i}.org" for i in range(11)}
    at_zero = auto_index.rotating_listing_seeds(seeds, allowed, hour=0)
    at_one = auto_index.rotating_listing_seeds(seeds, allowed, hour=1)
    assert len(at_zero) == len(at_one) == 8
    assert at_zero[0] != at_one[0]
    assert set(at_zero + at_one) == set(seeds)
    assert auto_index.rotating_listing_seeds(seeds, {"unknown.org"}, hour=0) == ()


def test_unapproved_cannot_be_discovered_as_listings():
    seeds = (_seed("https://unapproved.org/a", "listing"),)
    assert auto_index.rotating_listing_seeds(seeds, set(), hour=10) == ()


@pytest.mark.asyncio
async def test_cycle_registers_every_seed_but_only_queues_approved_content(monkeypatch):
    """Neither a supplied URL nor a discovered offsite link grants a license."""
    class FakeSession:
        async def scalars(self, statement):
            return ["example.org"]

        async def execute(self, statement):
            return SimpleNamespace(all=lambda: [
                ("https://example.org/story", "Existing", "Example"),
                ("https://unapproved.org/story", "Forbidden", "Other"),
            ])

    class FakeContext:
        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSessions:
        def __call__(self):
            return FakeContext()

        def begin(self):
            return FakeContext()

    page = SimpleNamespace(
        url="https://example.org/aktualnosci", media_type="text/html", robots="",
        content=(
            b'<a href="/news/today">Today on the official website</a>'
            b'<a href="https://unapproved.org/news">Offsite unapproved article</a>'
        ),
    )
    web = SimpleNamespace(read_article=AsyncMock(return_value=page))
    saved = AsyncMock()
    queued = AsyncMock()
    monkeypatch.setattr(auto_index, "store_discovered_metadata", saved)
    monkeypatch.setattr(auto_index, "enqueue_article", queued)
    seeds = (
        _seed("https://example.org/aktualnosci", "listing"),
        _seed("https://unapproved.org/aktualnosci", "listing"),
    )
    summary = await auto_index.run_cycle(FakeSessions(), web, seeds=seeds)
    assert summary["seeds"] == 2
    assert summary["approved_hosts"] == 1
    assert summary["scheduled_documents"] == 1
    assert summary["discovered_links"] == 1
    assert saved.await_count == 3  # both supplied seeds and one approved discovery
    assert queued.await_count == 2
    assert all("unapproved.org" not in str(call) for call in queued.await_args_list)
    web.read_article.assert_awaited_once()
