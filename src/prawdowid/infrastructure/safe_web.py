"""Bounded HTTP fetches with per-hop DNS validation and pinned connections."""

import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from ipaddress import IPv6Address, ip_address, ip_network
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from prawdowid.domain.urls import parse_http_url

USER_AGENT = "PRAWDOWID/0.1"
REDIRECTS = {301, 302, 303, 307, 308}
TEXT_CONTENT = {"text/html", "application/xhtml+xml", "text/plain"}
NAT64 = (ip_network("64:ff9b::/96"), ip_network("64:ff9b:1::/48"))
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


class FetchError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def resolve_dns(host: str, port: int) -> tuple[str, ...]:
    entries = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(entry[4][0] for entry in entries))


def is_public_address(value: str) -> bool:
    addr = ip_address(value)
    return (
        addr.is_global
        and not addr.is_multicast
        and not addr.is_reserved
        and not (
            isinstance(addr, IPv6Address)
            and (
                addr.ipv4_mapped
                or addr.sixtofour
                or addr.teredo
                or addr.scope_id
                or any(addr in prefix for prefix in NAT64)
            )
        )
    )


@dataclass(frozen=True)
class WebPage:
    url: str
    content: bytes
    status: int
    robots: str = ""


@dataclass(frozen=True)
class Article:
    title: str | None = None
    body: str | None = None
    published_at: datetime | None = None


class SafeWeb:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        resolve: Resolver = resolve_dns,
        max_bytes: int = 2 * 1024 * 1024,
        timeout: float = 8,
        max_redirects: int = 3,
    ):
        self.client = client
        # The pinned IP is shared across publishers and cannot scope a cookie jar.
        self.client.cookies = CookieJar(policy=DefaultCookiePolicy(allowed_domains=()))
        self.resolve = resolve
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.max_redirects = max_redirects

    async def read(self, url: str) -> WebPage:
        return await self._read(url, article=False)

    async def read_article(self, url: str) -> WebPage:
        return await self._read(url, article=True)

    async def _read(self, url: str, *, article: bool) -> WebPage:
        try:
            async with asyncio.timeout(self.timeout):
                for _ in range(self.max_redirects + 1):
                    if article:
                        await self._check_robots(url)
                    page, redirect = await self._request(url)
                    if redirect is None:
                        if page.status != 200:
                            raise FetchError("source_unavailable")
                        return page
                    url = urljoin(url, redirect)
        except (TimeoutError, httpx.TimeoutException):
            raise FetchError("timeout") from None
        except (httpx.HTTPError, OSError):
            raise FetchError("source_unavailable") from None
        except ValueError:
            raise FetchError("unsafe_url") from None
        raise FetchError("redirect_limit")

    async def _check_robots(self, url: str) -> None:
        parts = parse_http_url(url)
        target = f"{parts.scheme}://{parts.netloc}/robots.txt"
        for _ in range(3):
            page, redirect = await self._request(target)
            if redirect is None:
                break
            target = urljoin(target, redirect)
        if page.status in {404, 410}:
            return
        if page.status != 200 or redirect is not None:
            raise FetchError("robots_unavailable")
        policy = RobotFileParser()
        policy.parse(page.content.decode("utf-8", errors="replace").splitlines())
        if not policy.can_fetch(USER_AGENT, url):
            raise FetchError("robots_denied")

    async def _request(self, url: str) -> tuple[WebPage, str | None]:
        parts = parse_http_url(url)
        host = parts.hostname.encode("idna").decode("ascii")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in {80, 443}:
            raise FetchError("unsafe_url")
        try:
            ip_address(host)
        except ValueError:
            addresses = await self.resolve(host, port)
        else:
            addresses = (host,)
        if not addresses or not all(is_public_address(address) for address in addresses):
            raise FetchError("unsafe_url")
        pinned = httpx.URL(url).copy_with(host=addresses[0])
        headers = {
            "Host": httpx.URL(url).netloc.decode("ascii"),
            "Accept": "text/html,text/plain,application/xhtml+xml",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
        async with self.client.stream(
            "GET",
            pinned,
            headers=headers,
            follow_redirects=False,
            extensions={"sni_hostname": host},
        ) as response:
            status = response.status_code
            if status in REDIRECTS:
                target = response.headers.get("location")
                if not target:
                    raise FetchError("source_unavailable")
                return WebPage(url, b"", status), target
            if status != 200:
                return WebPage(url, b"", status), None
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if encoding != "identity" or content_type not in TEXT_CONTENT:
                raise FetchError("unsupported_content")
            result = bytearray()
            async for chunk in response.aiter_bytes():
                if len(result) + len(chunk) > self.max_bytes:
                    raise FetchError("body_limit")
                result.extend(chunk)
            return WebPage(
                url, bytes(result), status, response.headers.get("X-Robots-Tag", "")
            ), None


def extract_article(raw: bytes, *, allow_text: bool, robots: str = "") -> Article:
    soup = BeautifulSoup(raw, "html.parser")
    og_title = soup.find("meta", property="og:title")
    title = str(og_title.get("content", "")).strip() if og_title else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    date_tag = soup.find("meta", property="article:published_time")
    date = _published_at(str(date_tag.get("content", ""))) if date_tag else None
    robots_tags = soup.find_all("meta", attrs={"name": ("robots", "googlebot")})
    directives = f"{robots} " + " ".join(str(tag.get("content", "")) for tag in robots_tags)
    article = soup.find("article") or soup.find("main")
    if (
        not allow_text
        or any(flag in directives.lower() for flag in ("noarchive", "nosnippet"))
        or not article
        or soup.select_one(
            "[data-paywall], .paywall, [itemprop=isAccessibleForFree][content=false]"
        )
    ):
        return Article(title[:500] or None, None, date)
    for tag in article.select("script,style,nav,aside,footer,form,iframe,[data-nosnippet]"):
        tag.decompose()
    paragraphs = [" ".join(p.get_text(" ", strip=True).split()) for p in article.find_all("p")]
    body = "\n\n".join(part for part in paragraphs if part)
    return Article(title[:500] or None, body or None, date)


def _published_at(value: str) -> datetime | None:
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return None
    return date.astimezone(UTC) if date.tzinfo else None
