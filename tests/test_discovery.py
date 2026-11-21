import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from prawdowid.domain.models import DocumentCandidate, EventQuery, EvidencePassage
from prawdowid.infrastructure.discovery import (
    DiscoveryService,
    FactCheckDiscovery,
    GdeltDiscovery,
    GoogleNewsRssDiscovery,
)

EVENT = EventQuery("Nowa linia tramwajowa", entities=("Warszawa",))
RSS = b"""<rss version="2.0"><channel><title>News</title>
<item><title>Otwarto nowa linie</title><link>https://news.example/story?utm_source=rss</link>
<source url="https://news.example">News Publisher</source>
<pubDate>Sat, 21 Nov 2026 10:00:00 GMT</pubDate>
<description>Unverified provider snippet</description></item>
<item><title>Invalid URL</title><link>file:///tmp/page</link></item>
</channel></rss>"""
ARTICLE = {
    "url": "https://official.example/notice",
    "title": "Official notice",
    "domain": "official.example",
    "seendate": "20261121T100000Z",
    "snippet": "Extraneous content",
}


@pytest.mark.asyncio
async def test_normalizes_polish_rss_and_gdelt_as_metadata_only():
    async def reply(request):
        assert (
            "Warszawa"
            in request.url.params["q" if request.url.host == "news.google.com" else "query"]
        )
        if request.url.host == "news.google.com":
            assert request.url.params["ceid"] == "PL:pl"
            return httpx.Response(200, content=RSS)
        assert request.url.path == "/api/v2/doc/doc"
        assert request.url.params["mode"] == "artlist"
        assert request.url.params["format"] == "json"
        return httpx.Response(200, json={"articles": [ARTICLE]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        discovery = DiscoveryService([GoogleNewsRssDiscovery(client), GdeltDiscovery(client)])
        report = await discovery.discover(EVENT)

    assert not report.errors
    rss, gdelt = report.candidates
    assert rss.canonical_url == "https://news.example/story"
    assert rss.publisher == "News Publisher"
    assert rss.published_at == datetime(2026, 11, 21, 10, tzinfo=UTC)
    assert gdelt.domain == "official.example"
    assert gdelt.published_at is None
    assert gdelt.seen_at == datetime(2026, 11, 21, 10, tzinfo=UTC)
    for candidate in report.candidates:
        assert isinstance(candidate, DocumentCandidate)
        assert not isinstance(candidate, EvidencePassage)
        assert "snippet" not in repr(candidate).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (b"<rss><channel></channel></rss>", None),
        (b"<html>unexpected content</html>", "malformed_provider_response"),
        (b"<rss>", "malformed_provider_response"),
    ],
)
async def test_distinguishes_empty_rss_from_malformed_payload(body, expected_code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as client:
        report = await DiscoveryService([GoogleNewsRssDiscovery(client)]).discover(EVENT)

    assert not report.candidates
    assert [error.error_code for error in report.errors] == (
        [expected_code] if expected_code else []
    )


@pytest.mark.asyncio
async def test_provider_rate_cap_does_not_send_additional_http_requests():
    calls = 0

    def reply(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"articles": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        discovery = DiscoveryService([GdeltDiscovery(client)], max_requests_per_window=1)
        assert not (await discovery.discover(EVENT)).errors
        blocked = await discovery.discover(EVENT)

    assert calls == 1
    assert blocked.errors[0].error_code == "provider_rate_limited"
    assert blocked.errors[0].retryable


@pytest.mark.asyncio
async def test_rate_limited_provider_opens_circuit_without_discarding_other_results():
    calls = 0

    def reply(request):
        nonlocal calls
        if request.url.host == "news.google.com":
            return httpx.Response(200, content=RSS)
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        discovery = DiscoveryService([GoogleNewsRssDiscovery(client), GdeltDiscovery(client)])
        first = await discovery.discover(EVENT)
        second = await discovery.discover(EVENT)

    assert calls == 1
    for report in (first, second):
        assert len(report.candidates) == 1
        assert report.errors[0].provider == "gdelt"
        assert report.errors[0].error_code == "provider_rate_limited"
        assert report.errors[0].at.tzinfo is not None


@pytest.mark.asyncio
async def test_timeout_and_malformed_json_are_typed_without_evidence():
    async def reply(request):
        if request.url.host == "news.google.com":
            await asyncio.Event().wait()
        return httpx.Response(200, json={"articles": "unrecognized data"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        discovery = DiscoveryService(
            [GoogleNewsRssDiscovery(client), GdeltDiscovery(client)], timeout_seconds=0.05
        )
        report = await discovery.discover(EVENT)

    assert not report.candidates
    assert [error.error_code for error in report.errors] == [
        "timeout",
        "malformed_provider_response",
    ]


@pytest.mark.asyncio
async def test_deduplicates_candidates_across_providers_before_applying_result_limit():
    def reply(request):
        if request.url.host == "news.google.com":
            return httpx.Response(200, content=RSS)
        duplicate = {**ARTICLE, "url": "https://news.example/story#summary"}
        return httpx.Response(200, json={"articles": [duplicate, ARTICLE]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = DiscoveryService(
            [GoogleNewsRssDiscovery(client), GdeltDiscovery(client)], max_results=2
        )
        report = await service.discover(EVENT)

    assert [entry.canonical_url for entry in report.candidates] == [
        "https://news.example/story",
        "https://official.example/notice",
    ]


@pytest.mark.asyncio
async def test_optional_fact_check_auth_and_reviews_stay_metadata_only():
    def reply(request):
        assert request.url.path == "/v1alpha1/claims:search"
        assert request.headers["X-Goog-Api-Key"] == "test-key"
        assert "test-key" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "claims": [
                    {
                        "text": "An input claim",
                        "claimReview": [
                            {
                                "publisher": {"name": "Fact Reviewer"},
                                "url": "https://facts.example/review",
                                "title": "Review title",
                                "textualRating": "True",
                                "reviewDate": "2026-11-21T10:00:00Z",
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        provider = FactCheckDiscovery(client, api_key="test-key")
        report = await DiscoveryService([provider]).discover(EVENT)

    assert len(report.candidates) == 1
    assert report.candidates[0].publisher == "Fact Reviewer"
    assert "True" not in repr(report.candidates[0])
