"""Bounded news discovery. Provider metadata is suitable for finding URLs."""

import asyncio
import hashlib
import json
import re
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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


class DiscoveryCache(Protocol):
    async def get(self, provider: str, event: EventQuery) -> Sequence[DocumentCandidate] | None: ...

    async def put(self, provider: str, event: EventQuery, candidates: Sequence[DocumentCandidate]) -> None: ...


class DailyBudget(Protocol):
    async def claim(self, provider: str, credits: int = 1) -> bool: ...


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
        cache: DiscoveryCache | None = None,
        fallback_providers: Iterable[DiscoveryProvider] = (),
        fallback_budget: DailyBudget | None = None,
        fallback_min_publishers: int = 2,
    ):
        if min(max_concurrency, max_results, max_requests_per_window, breaker_failures) < 1:
            raise ValueError("discovery limits must be positive")
        if any(
            not isfinite(value) or value <= 0
            for value in (timeout_seconds, rate_window_seconds, breaker_seconds)
        ):
            raise ValueError("discovery durations must be finite and positive")
        self.providers = tuple(providers)
        self.fallback_providers = tuple(fallback_providers)
        if fallback_min_publishers < 1:
            raise ValueError("fallback_min_publishers must be positive")
        self.fallback_min_publishers = fallback_min_publishers
        self.cache = cache
        self.fallback_budget = fallback_budget
        all_providers = self.providers + self.fallback_providers
        self._provider_state = {provider.name: ProviderState() for provider in all_providers}
        if len(self._provider_state) != len(all_providers):
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
        primary = await asyncio.gather(
            *(self._search(provider, event) for provider in self.providers)
        )
        selected = {}
        errors = [error for _, error in primary if error is not None]
        for rows in zip_longest(*(candidates for candidates, _ in primary)):
            for candidate in rows:
                if candidate is not None:
                    selected.setdefault(candidate.canonical_url, candidate)
        domains = {
            (getattr(item, "domain", None) or urlsplit(item.canonical_url).hostname or "")
            .casefold().removeprefix("www.")
            for item in selected.values()
        }
        domains.discard("")
        if len(domains) < self.fallback_min_publishers and self.fallback_providers:
            fallback = await asyncio.gather(
                *(self._search(provider, event, fallback=True) for provider in self.fallback_providers)
            )
            errors.extend(error for _, error in fallback if error is not None)
            for rows in zip_longest(*(candidates for candidates, _ in fallback)):
                for candidate in rows:
                    if candidate is not None:
                        selected.setdefault(candidate.canonical_url, candidate)
        return DiscoveryReport(tuple(list(selected.values())[:self.max_results]), tuple(errors))

    async def _search(self, provider: DiscoveryProvider, event: EventQuery, *, fallback=False):
        state = self._provider_state[provider.name]
        started = False
        try:
            async with asyncio.timeout(self.timeout_seconds), self._slots:
                # Do not spend free-tier credits on events outside its 48-hour window.
                if fallback and isinstance(provider, NewsDataDiscovery) and not provider.can_search(event):
                    return (), None
                if self.cache is not None:
                    cached = await self.cache.get(provider.name, event)
                    if cached is not None:
                        return cached, None
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
                if (
                    fallback
                    and self.fallback_budget is not None
                    and not await self.fallback_budget.claim(provider.name)
                ):
                    return (), self._error(provider.name, "provider_rate_limited", False)
                state.recent_requests.append(now)
                started = True
                candidates = await provider.discover(event, self.max_results)
                state.consecutive_failures = 0
                if self.cache is not None:
                    await self.cache.put(provider.name, event, candidates)
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


class NewsDataDiscovery(_HttpDiscovery):
    """Optional 12h-delayed NewsData.io free-tier fallback (metadata only)."""

    name = "newsdata"
    URL = "https://newsdata.io/api/1/latest"

    def __init__(self, client: httpx.AsyncClient, *, api_key: str):
        super().__init__(client)
        if not api_key.strip():
            raise ValueError("NewsData API key is required")
        self._api_key = api_key

    def can_search(self, event: EventQuery) -> bool:
        return not (
            event.occurred_before is not None
            and _utc(event.occurred_before) < datetime.now(UTC) - timedelta(hours=48)
        )

    async def discover(self, event: EventQuery, limit: int) -> tuple[DiscoveredArticle, ...]:
        query = _query(event)
        if not query:
            return ()
        body = _json_object(await self._get(
            self.URL,
            {"q": query[:512], "language": "pl", "size": min(limit, 10)},
            {"X-ACCESS-KEY": self._api_key},
        ))
        if body.get("status") != "success" or not isinstance(body.get("results"), list):
            raise ProviderFailure(MALFORMED, retryable=False)
        candidates = []
        for row in body["results"]:
            if not isinstance(row, dict):
                continue
            candidate = _candidate(
                self.name, row.get("link"), row.get("title"), row.get("source_name"),
                _timestamp(row.get("pubDate"), datetime.fromisoformat),
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


_QUERY_STOPWORDS = {
    "od", "do", "dla", "oraz", "jest", "była", "było", "roku", "dnia",
    "we", "na", "pod", "po", "się", "taki", "także", "który", "która",
}


def _query(event: EventQuery) -> str:
    # External search works better with event anchors than with whole sentences.
    # This is still untrusted input, and will be sent only on explicit opt-in.
    values = (*event.entities, event.location or "", event.query_text)
    terms = dict.fromkeys(
        word for word in WORDS.findall(" ".join(values))
        if len(word) > 2 and word.casefold() not in _QUERY_STOPWORDS
    )
    return " ".join(list(terms)[:10])[:256].rstrip()


def cache_key(provider: str, event: EventQuery) -> str:
    """Hash public search anchors and date window, not the original submitted text."""
    content = json.dumps(
        [provider, _query(event),
         event.occurred_after.isoformat() if event.occurred_after else None,
         event.occurred_before.isoformat() if event.occurred_before else None],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(content.encode()).hexdigest()


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
