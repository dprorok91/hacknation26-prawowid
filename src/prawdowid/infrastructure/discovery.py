"""Bounded news discovery. Provider metadata is suitable for finding URLs."""

import asyncio
import json
import re
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from itertools import zip_longest
from math import isfinite
from time import monotonic
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from prawdowid.domain.models import DocumentCandidate, EventQuery
from prawdowid.domain.urls import canonical_url

MAX_RESPONSE_BYTES = 1024 * 1024
WORDS = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
MALFORMED = "malformed_provider_response"


@dataclass(frozen=True)
class DiscoveredArticle(DocumentCandidate):
    domain: str
    seen_at: datetime | None = None


@dataclass(frozen=True)
class DiscoveryError:
    provider: str
    error_code: str
    retryable: bool
    at: datetime


@dataclass(frozen=True)
class DiscoveryReport:
    candidates: tuple[DocumentCandidate, ...] = ()
    errors: tuple[DiscoveryError, ...] = ()


class ProviderFailure(Exception):
    def __init__(self, error_code: str, *, retryable: bool, retry_after: float = 0):
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable
        self.retry_after = retry_after


class DiscoveryProvider(Protocol):
    name: str

    async def discover(self, event: EventQuery, limit: int) -> Sequence[DocumentCandidate]: ...


@dataclass
class ProviderState:
    recent_requests: deque[float] = field(default_factory=deque)
    consecutive_failures: int = 0
    blocked_until: float = 0
    blocked_code: str = "source_unavailable"


class DiscoveryService:
    def __init__(
        self,
        providers: Iterable[DiscoveryProvider],
        *,
        timeout_seconds: float = 4,
        max_concurrency: int = 2,
        max_results: int = 10,
        max_requests_per_window: int = 4,
        rate_window_seconds: float = 60,
        breaker_failures: int = 3,
        breaker_seconds: float = 30,
    ):
        if min(max_concurrency, max_results, max_requests_per_window, breaker_failures) < 1:
            raise ValueError("discovery limits must be positive")
        if any(
            not isfinite(value) or value <= 0
            for value in (timeout_seconds, rate_window_seconds, breaker_seconds)
        ):
            raise ValueError("discovery durations must be finite and positive")
        self.providers = tuple(providers)
        self._provider_state = {provider.name: ProviderState() for provider in self.providers}
        if len(self._provider_state) != len(self.providers):
            raise ValueError("discovery provider names must be unique")
        self.timeout_seconds = timeout_seconds
        self.max_results = min(max_results, 250)
        self.max_requests_per_window = max_requests_per_window
        self.rate_window_seconds = rate_window_seconds
        self.breaker_failures = breaker_failures
        self.breaker_seconds = breaker_seconds
        self._slots = asyncio.Semaphore(max_concurrency)

    async def discover(self, event: EventQuery) -> DiscoveryReport:
        if not _query(event):
            return DiscoveryReport()
        results = await asyncio.gather(
            *(self._search(provider, event) for provider in self.providers)
        )
        selected = {}
        errors = tuple(error for _, error in results if error is not None)
        for rows in zip_longest(*(candidates for candidates, _ in results)):
            for candidate in rows:
                if candidate is not None:
                    selected.setdefault(candidate.canonical_url, candidate)
                    if len(selected) >= self.max_results:
                        return DiscoveryReport(tuple(selected.values()), errors)
        return DiscoveryReport(tuple(selected.values()), errors)

    async def _search(self, provider: DiscoveryProvider, event: EventQuery):
        state = self._provider_state[provider.name]
        started = False
        try:
            async with asyncio.timeout(self.timeout_seconds), self._slots:
                now = monotonic()
                if state.blocked_until > now:
                    return (), self._error(provider.name, state.blocked_code, True)
                while (
                    state.recent_requests
                    and state.recent_requests[0] <= now - self.rate_window_seconds
                ):
                    state.recent_requests.popleft()
                if len(state.recent_requests) >= self.max_requests_per_window:
                    return (), self._error(provider.name, "provider_rate_limited", True)
                state.recent_requests.append(now)
                started = True
                candidates = await provider.discover(event, self.max_results)
                state.consecutive_failures = 0
                return candidates, None
        except (TimeoutError, httpx.TimeoutException):
            failure = ProviderFailure("timeout", retryable=True)
        except httpx.RequestError:
            failure = ProviderFailure("source_unavailable", retryable=True)
        except ProviderFailure as error:
            failure = error

        if started:
            state.consecutive_failures += 1
            if (
                failure.error_code == "provider_rate_limited"
                or state.consecutive_failures >= self.breaker_failures
            ):
                state.blocked_until = max(
                    state.blocked_until,
                    monotonic() + max(failure.retry_after, self.breaker_seconds),
                )
                state.blocked_code = failure.error_code
        return (), self._error(provider.name, failure.error_code, failure.retryable)

    @staticmethod
    def _error(provider: str, code: str, retryable: bool) -> DiscoveryError:
        return DiscoveryError(provider, code, retryable, datetime.now(UTC))


class _HttpDiscovery:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _get(self, url: str, params: dict, headers: dict[str, str] | None = None) -> bytes:
        request_headers = {"Accept-Encoding": "identity", **(headers or {})}
        async with self._client.stream(
            "GET", url, params=params, headers=request_headers, follow_redirects=False
        ) as response:
            status = response.status_code
            if status == 429:
                raise ProviderFailure(
                    "provider_rate_limited",
                    retryable=True,
                    retry_after=_retry_after(response.headers.get("Retry-After")),
                )
            if not response.is_success:
                raise ProviderFailure(
                    "source_unavailable", retryable=status >= 500 or status == 408
                )
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ProviderFailure(MALFORMED, retryable=False)
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ProviderFailure(MALFORMED, retryable=False)
                body.extend(chunk)
            return bytes(body)


class GoogleNewsRssDiscovery(_HttpDiscovery):
    name = "google_news_rss"
    URL = "https://news.google.com/rss/search"

    async def discover(self, event: EventQuery, limit: int) -> tuple[DiscoveredArticle, ...]:
        query = _query(event)
        if event.occurred_after is not None:
            query += f" after:{_utc(event.occurred_after).date()}"
        if event.occurred_before is not None:
            query += f" before:{_utc(event.occurred_before).date()}"
        content = await self._get(self.URL, {"q": query, "hl": "pl", "gl": "PL", "ceid": "PL:pl"})
        try:
            xml = content.decode("utf-8-sig")
            # This fixed RSS endpoint requires neither DTDs nor custom XML entities.
            if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
                raise ValueError
            rss = ElementTree.fromstring(xml)
        except (ValueError, ElementTree.ParseError):
            raise ProviderFailure(MALFORMED, retryable=False) from None
        channel = rss.find("channel") if rss.tag == "rss" else None
        if channel is None:
            raise ProviderFailure(MALFORMED, retryable=False)

        candidates = []
        for item in channel.iterfind("item"):
            source = item.find("source")
            candidate = _candidate(
                self.name,
                item.findtext("link"),
                item.findtext("title"),
                source.text if source is not None else None,
                _timestamp(item.findtext("pubDate"), parsedate_to_datetime),
                domain_hint=_domain_hint(source.get("url")) if source is not None else None,
            )
            if candidate is not None:
                candidates.append(candidate)
                if len(candidates) >= limit:
                    break
        return tuple(candidates)


class GdeltDiscovery(_HttpDiscovery):
    name = "gdelt"
    URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def discover(self, event: EventQuery, limit: int) -> tuple[DiscoveredArticle, ...]:
        params = {
            "query": f"{_query(event)} sourcelang:polish",
            "mode": "artlist",
            "format": "json",
            "maxrecords": max(1, min(limit, 250)),
        }
        for field_name, bound in (
            ("startdatetime", event.occurred_after),
            ("enddatetime", event.occurred_before),
        ):
            if bound is not None:
                params[field_name] = _utc(bound).strftime("%Y%m%d%H%M%S")
        body = _json_object(await self._get(self.URL, params))
        articles = body.get("articles")
        if not isinstance(articles, list):
            raise ProviderFailure(MALFORMED, retryable=False)
        candidates = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            candidate = _candidate(
                self.name,
                article.get("url"),
                article.get("title"),
                None,
                None,
                seen_at=_timestamp(article.get("seendate"), _gdelt_seen),
            )
            if candidate is not None:
                candidates.append(candidate)
                if len(candidates) >= limit:
                    break
        return tuple(candidates)


class FactCheckDiscovery(_HttpDiscovery):
    name = "google_fact_check"
    URL = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

    def __init__(self, client: httpx.AsyncClient, *, api_key: str):
        super().__init__(client)
        if not api_key.strip():
            raise ValueError("an explicit Fact Check API key is required")
        self._api_key = api_key

    async def discover(self, event: EventQuery, limit: int) -> tuple[DiscoveredArticle, ...]:
        body = _json_object(
            await self._get(
                self.URL,
                {"query": _query(event), "languageCode": "pl", "pageSize": min(limit, 100)},
                {"X-Goog-Api-Key": self._api_key},
            )
        )
        claims = body.get("claims", [])
        if not isinstance(claims, list) or "error" in body:
            raise ProviderFailure(MALFORMED, retryable=False)
        candidates = []
        for claim in claims:
            reviews = claim.get("claimReview") if isinstance(claim, dict) else None
            if not isinstance(reviews, list):
                continue
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                publisher = review.get("publisher")
                candidate = _candidate(
                    self.name,
                    review.get("url"),
                    review.get("title"),
                    publisher.get("name") if isinstance(publisher, dict) else None,
                    _timestamp(review.get("reviewDate"), datetime.fromisoformat),
                )
                if candidate is not None:
                    candidates.append(candidate)
                    if len(candidates) >= limit:
                        return tuple(candidates)
        return tuple(candidates)


def _query(event: EventQuery) -> str:
    values = (event.query_text, *event.entities, event.location or "")
    terms = dict.fromkeys(WORDS.findall(" ".join(values)))
    return " ".join(terms)[:256].rstrip()


def _candidate(
    provider: str,
    url: object,
    headline: object,
    publisher: object,
    published_at: datetime | None,
    *,
    seen_at: datetime | None = None,
    domain_hint: str | None = None,
) -> DiscoveredArticle | None:
    if not isinstance(url, str) or not _display_text(headline) or len(url) > 4096:
        return None
    try:
        normalized = canonical_url(url.strip())
    except ValueError:
        return None
    domain = domain_hint or urlsplit(normalized).hostname
    return DiscoveredArticle(
        normalized,
        _display_text(publisher) or domain,
        _display_text(headline),
        published_at,
        provider,
        domain,
        seen_at,
    )


def _domain_hint(value: str | None) -> str | None:
    try:
        return urlsplit(canonical_url(value)).hostname if value else None
    except ValueError:
        return None


def _display_text(value: object) -> str:
    return " ".join(value.split())[:500] if isinstance(value, str) else ""


def _utc(date: datetime) -> datetime:
    return date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)


def _timestamp(value: object, parse: Callable[[str], datetime]) -> datetime | None:
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        return _utc(parse(value))
    except (ValueError, OverflowError):
        return None


def _gdelt_seen(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def _json_object(body: bytes) -> dict:
    try:
        decoded = json.loads(body)
    except (ValueError, RecursionError):
        raise ProviderFailure(MALFORMED, retryable=False) from None
    if not isinstance(decoded, dict):
        raise ProviderFailure(MALFORMED, retryable=False)
    return decoded


def _retry_after(value: str | None) -> float:
    if value is None:
        return 0
    if value.isascii() and value.isdigit() and len(value) <= 10:
        return float(value)
    date = _timestamp(value, parsedate_to_datetime)
    return max(0, (date - datetime.now(UTC)).total_seconds()) if date is not None else 0
