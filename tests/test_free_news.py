"""Offline news-provider budget/cache behavior; no live network or keys."""

from datetime import UTC, datetime

import httpx
import pytest

from prawdowid.domain.models import EventQuery
from prawdowid.infrastructure.discovery import (
    DiscoveryService,
    GdeltDiscovery,
    GoogleNewsRssDiscovery,
    NewsDataDiscovery,
    cache_key,
)

EVENT = EventQuery("tramwaje Kraków")
RSS_TWO = b"""<rss version="2.0"><channel><title>news</title>
<item><title>A</title><link>https://example-a.pl/a</link><source>Portal A</source></item>
<item><title>B</title><link>https://example-b.pl/b</link><source>Portal B</source></item>
</channel></rss>"""
RSS_ONE = b"""<rss version="2.0"><channel><title>news</title>
<item><title>A</title><link>https://example-a.pl/a</link><source>Portal A</source></item>
</channel></rss>"""
NEWS_RESPONSE = {"status": "success", "results": [
    {"title": "Komunikat", "link": "https://newsdata-example.pl/article?utm_source=api",
     "source_name": "Portal C", "pubDate": "2026-11-21 11:00:00",
     "description": "UNTRUSTED snippet", "content": "UNTRUSTED body"},
]}


class MemoryCache:
    def __init__(self):
        self.items = {}
        self.gets = 0
        self.puts = 0

    async def get(self, provider, event):
        self.gets += 1
        return self.items.get(cache_key(provider, event))

    async def put(self, provider, event, candidates):
        self.puts += 1
        self.items[cache_key(provider, event)] = tuple(candidates)


class Budget:
    def __init__(self, remaining):
        self.remaining = remaining
        self.claims = 0

    async def claim(self, provider, credits=1):
        self.claims += 1
        if self.remaining < credits:
            return False
        self.remaining -= credits
        return True


@pytest.mark.asyncio
async def test_newsdata_header_auth_validates_and_discards_provider_article_body():
    requests = []

    def reply(request):
        requests.append(request)
        assert request.headers["X-ACCESS-KEY"] == "secret-fixture"
        assert "secret-fixture" not in str(request.url)
        assert request.url.params["language"] == "pl"
        assert int(request.url.params["size"]) <= 10
        return httpx.Response(200, json=NEWS_RESPONSE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        report = await DiscoveryService(
            [NewsDataDiscovery(client, api_key="secret-fixture")]
        ).discover(EVENT)
    assert len(requests) == 1
    assert not report.errors
    assert len(report.candidates) == 1
    result = report.candidates[0]
    assert result.canonical_url == "https://newsdata-example.pl/article"
    assert result.published_at == datetime(2026, 11, 21, 11, tzinfo=UTC)
    assert result.publisher == "Portal C"
    assert "UNTRUSTED" not in repr(result)


@pytest.mark.asyncio
async def test_newsdata_rejects_error_response_or_bad_payload():
    for payload in ({"status": "error", "results": []}, {"status": "success", "results": {}}):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, payload=payload: httpx.Response(200, json=payload))
        ) as client:
            outcome = await DiscoveryService(
                [NewsDataDiscovery(client, api_key="unused")]
            ).discover(EVENT)
        assert not outcome.candidates
        assert outcome.errors[0].error_code == "malformed_provider_response"


@pytest.mark.asyncio
async def test_two_independent_primary_publishers_skip_paid_fallback():
    hits = []

    def reply(request):
        hits.append(request.url.host)
        if request.url.host == "news.google.com":
            return httpx.Response(200, content=RSS_TWO)
        if request.url.host == "api.gdeltproject.org":
            return httpx.Response(200, json={"articles": []})
        raise AssertionError("Fallback provider was called unexpectedly")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = DiscoveryService(
            [GoogleNewsRssDiscovery(client), GdeltDiscovery(client)],
            fallback_providers=[NewsDataDiscovery(client, api_key="unused")],
            fallback_budget=Budget(2),
        )
        result = await service.discover(EVENT)
        assert len(result.candidates) == 2
        assert not result.errors
    assert hits.count("newsdata.io") == 0


@pytest.mark.asyncio
async def test_fallback_reserves_credit_only_on_uncached_request():
    hits = []
    cache = MemoryCache()
    budget = Budget(1)

    def reply(request):
        hits.append(request.url.host)
        if request.url.host == "news.google.com":
            return httpx.Response(200, content=RSS_ONE)
        if request.url.host == "api.gdeltproject.org":
            return httpx.Response(200, json={"articles": []})
        return httpx.Response(200, json=NEWS_RESPONSE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = DiscoveryService(
            [GoogleNewsRssDiscovery(client), GdeltDiscovery(client)],
            fallback_providers=[NewsDataDiscovery(client, api_key="unused")],
            fallback_budget=budget, cache=cache,
        )
        first = await service.discover(EVENT)
        second = await service.discover(EVENT)
        assert len(first.candidates) == 2
        assert second.candidates == first.candidates
        assert not first.errors and not second.errors
        assert budget.claims == 1
        assert hits.count("newsdata.io") == 1
        assert len(hits) == 3  # one call to each provider, then cached results
        assert cache.puts == 3


@pytest.mark.asyncio
async def test_exhausted_budget_never_calls_fallback():
    requests = []

    def reply(request):
        requests.append(request.url.host)
        if request.url.host == "news.google.com":
            return httpx.Response(200, content=RSS_ONE)
        return httpx.Response(200, json=NEWS_RESPONSE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        outcome = await DiscoveryService(
            [GoogleNewsRssDiscovery(client)],
            fallback_providers=[NewsDataDiscovery(client, api_key="unused")],
            fallback_budget=Budget(0),
        ).discover(EVENT)
    assert len(outcome.candidates) == 1
    assert requests == ["news.google.com"]
    assert [(e.provider, e.error_code) for e in outcome.errors] == [
        ("newsdata", "provider_rate_limited")
    ]


@pytest.mark.asyncio
async def test_successful_empty_search_is_cached_but_provider_errors_are_not():
    hits = 0
    cache = MemoryCache()

    def reply(request):
        nonlocal hits
        hits += 1
        return httpx.Response(200, json={"articles": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = DiscoveryService([GdeltDiscovery(client)], cache=cache)
        assert not (await service.discover(EVENT)).candidates
        assert not (await service.discover(EVENT)).candidates
        assert hits == 1
        await service.discover(EventQuery("inne wydarzenie"))
        assert hits == 2

    error_hits = 0
    def failed(request):
        nonlocal error_hits
        error_hits += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        service = DiscoveryService([GdeltDiscovery(client)], cache=MemoryCache())
        assert (await service.discover(EVENT)).errors
        assert (await service.discover(EVENT)).errors
    assert error_hits == 2


def test_event_fingerprint_differs_by_window_and_never_contains_raw_query():
    first = cache_key("gdelt", EventQuery("Kraków nowa linia"))
    second = cache_key("gdelt", EventQuery("Kraków nowa linia", occurred_after=datetime(2026, 1, 1, tzinfo=UTC)))
    assert first != second
    assert "Kraków" not in first
    assert len(first) == 64

@pytest.mark.asyncio
async def test_historical_event_skips_48_hour_fallback_without_spending_credits():
    budget = Budget(1)
    calls = []

    def reply(request):
        calls.append(request.url.host)
        return httpx.Response(200, content=RSS_ONE)

    event = EventQuery("Kraków strefa", occurred_before=datetime(2026, 1, 10, tzinfo=UTC))
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        outcome = await DiscoveryService(
            [GoogleNewsRssDiscovery(client)],
            fallback_providers=[NewsDataDiscovery(client, api_key="unused")],
            fallback_budget=budget,
        ).discover(event)
    assert len(outcome.candidates) == 1
    assert budget.claims == 0
    assert calls == ["news.google.com"]
