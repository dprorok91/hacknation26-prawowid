"""Optional, metadata-only lookups of prior fact-checks (never verdict evidence)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from prawdowid.domain.urls import canonical_url

GOOGLE_CLAIMS = "https://factchecktools.googleapis.com/v1alpha1/claims:search"
GOOGLE_RSS = "https://news.google.com/rss/search"
_FACTCHECK_SITES = ("demagog.org.pl", "konkret24.tvn24.pl", "sprawdzam.afp.com")
MAX_BODY = 512 * 1024


@dataclass(frozen=True)
class PriorReview:
    publisher: str
    title: str
    url: str
    reviewed_claim: str | None = None
    published_rating: str | None = None
    reviewed_at: str | None = None
    provider: str = ""

    def as_report(self, claim_id: str) -> dict:
        return {
            "claim_id": claim_id,
            "publisher": self.publisher,
            "title": self.title,
            "url": self.url,
            "reviewed_claim": self.reviewed_claim,
            "published_rating": self.published_rating,
            "reviewed_at": self.reviewed_at,
            "provider": self.provider,
            "match_status": "not_assessed",  # Matching requires separate evidence review.
        }


def _text(value: object, limit: int = 500) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _url(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return None
    try:
        url = canonical_url(value)
        if len(url) > 2048:
            return None
        host = urlsplit(url).hostname
        if not host:
            return None
        return url
    except ValueError:
        return None


async def _body(client: httpx.AsyncClient, url: str, *, params: dict, headers=None) -> bytes:
    async with client.stream(
        "GET", url, params=params, headers={"Accept-Encoding": "identity", **(headers or {})},
        follow_redirects=False, timeout=8,
    ) as response:
        response.raise_for_status()
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("Unexpected compression")
        result = bytearray()
        async for part in response.aiter_bytes():
            if len(result) + len(part) > MAX_BODY:
                raise ValueError("Fact-check response exceeds the size limit")
            result.extend(part)
        return bytes(result)


class GoogleClaimReviewAPI:
    """Official Google Fact Check Claim Search API; requires developer's API key."""

    def __init__(self, client: httpx.AsyncClient, api_key: str):
        if not api_key.strip():
            raise ValueError("A Google Fact Check API key is required")
        self.client = client
        self.api_key = api_key

    async def search(self, statement: str) -> tuple[PriorReview, ...]:
        raw = await _body(
            self.client, GOOGLE_CLAIMS,
            params={"query": statement[:350], "languageCode": "pl", "pageSize": 10},
            headers={"X-Goog-Api-Key": self.api_key},
        )
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("claims", []), list):
            raise TypeError("Malformed fact-check response")
        result = []
        for claim in data.get("claims", [])[:10]:
            if not isinstance(claim, dict) or not isinstance(claim.get("claimReview"), list):
                continue
            for review in claim["claimReview"][:3]:
                if not isinstance(review, dict):
                    continue
                link = _url(review.get("url"))
                publisher = review.get("publisher")
                name = _text(publisher.get("name")) if isinstance(publisher, dict) else ""
                if not link or not name:
                    continue
                result.append(PriorReview(
                    publisher=name,
                    title=_text(review.get("title")) or name,
                    url=link,
                    reviewed_claim=_text(claim.get("text")) or None,
                    published_rating=_text(review.get("textualRating"), 120) or None,
                    reviewed_at=_text(review.get("reviewDate"), 80) or None,
                    provider="google_fact_check",
                ))
        return tuple(result[:10])


class PolishFactcheckRSS:
    """Keyless discovery of Polish fact-check *links* via Google News RSS."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def search(self, statement: str) -> tuple[PriorReview, ...]:
        sites = " OR ".join(f"site:{site}" for site in _FACTCHECK_SITES)
        query = f"{statement[:180]} ({sites})"
        raw = await _body(
            self.client, GOOGLE_RSS,
            params={"q": query, "hl": "pl", "gl": "PL", "ceid": "PL:pl"},
        )
        xml = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError("Unsafe RSS response")
        root = ElementTree.fromstring(xml)
        channel = root.find("channel") if root.tag == "rss" else None
        if channel is None:
            raise ValueError("Invalid RSS response")
        result = []
        for item in channel.findall("item")[:10]:
            source = item.find("source")
            publisher = _text(source.text) if source is not None else ""
            source_url = _url(source.get("url")) if source is not None else None
            source_host = (urlsplit(source_url).hostname or "").removeprefix("www.") if source_url else ""
            if not any(
                source_host == host or source_host.endswith("." + host)
                for host in _FACTCHECK_SITES
            ):
                continue
            # RSS offers no ClaimReview rating, claim identity or fact-check verdict.
            link = _url(item.findtext("link"))
            if not link or not publisher:
                continue
            published = None
            try:
                raw_date = item.findtext("pubDate")
                published = parsedate_to_datetime(raw_date).isoformat() if raw_date else None
            except (ValueError, TypeError, OverflowError):
                pass
            result.append(PriorReview(
                publisher=publisher,
                title=_text(item.findtext("title")) or publisher,
                url=link, reviewed_at=published, provider="google_news_factcheck_sites",
            ))
        return tuple(result)


class FactCheckLookup:
    """Small bounded lookups cached per worker; separate from normal evidence retrieval."""

    def __init__(
        self,
        providers: tuple[object, ...],
        *,
        ttl_seconds: int = 43200,
        timeout_seconds: float = 9,
    ) -> None:
        self.providers = providers
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, tuple[PriorReview, ...], tuple[str, ...]]] = {}

    async def search(self, statement: str) -> tuple[tuple[PriorReview, ...], tuple[str, ...]]:
        key = statement.strip().casefold()
        cached = self._cache.get(key)
        now = monotonic()
        if cached is not None and now - cached[0] < self.ttl_seconds:
            return cached[1], cached[2]
        results = await asyncio.gather(
            *(self._one(item, statement) for item in self.providers)
        )
        found = {}
        errors = []
        for items, error in results:
            if error:
                errors.append(error)
            for item in items:
                found.setdefault(item.url, item)
        answer = (tuple(found.values())[:15], tuple(errors))
        # Cache only successful lookups. Transient failures must be retryable.
        if not errors:
            self._cache[key] = (now, *answer)
            if len(self._cache) > 128:
                self._cache.pop(next(iter(self._cache)))
        return answer

    async def _one(self, provider, statement: str):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await provider.search(statement), None
        except (TimeoutError, httpx.HTTPError, TypeError, ValueError, ElementTree.ParseError):
            return (), type(provider).__name__ + ": unavailable"
