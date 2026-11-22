"""Bounded HTTP fetches with per-hop DNS validation and pinned connections."""

import asyncio
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from ipaddress import IPv6Address, ip_address, ip_network
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag

from prawdowid.domain.urls import parse_http_url

USER_AGENT = "PRAWDOWID/0.1"
REDIRECTS = {301, 302, 303, 307, 308}
TEXT_CONTENT = {"text/html", "application/xhtml+xml", "text/plain"}
PDF_CONTENT = "application/pdf"
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
    media_type: str = "text/html"


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
                    page, redirect = await self._request(url, allow_pdf=article)
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

    async def _request(
        self, url: str, *, allow_pdf: bool = False
    ) -> tuple[WebPage, str | None]:
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
            if encoding != "identity" or (
                content_type not in TEXT_CONTENT
                and not (allow_pdf and content_type == PDF_CONTENT)
            ):
                raise FetchError("unsupported_content")
            result = bytearray()
            async for chunk in response.aiter_bytes():
                if len(result) + len(chunk) > self.max_bytes:
                    raise FetchError("body_limit")
                result.extend(chunk)
            return WebPage(
                url, bytes(result), status, response.headers.get("X-Robots-Tag", ""), content_type
            ), None


def extract_article(
    raw: bytes, *, allow_text: bool, robots: str = "", source_url: str = ""
) -> Article:
    soup = BeautifulSoup(raw, "html.parser")
    og_title = soup.find("meta", property="og:title")
    title = str(og_title.get("content", "")).strip() if og_title else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    date_tag = soup.find("meta", property="article:published_time")
    date = _published_at(str(date_tag.get("content", ""))) if date_tag else None
    robots_tags = soup.find_all("meta", attrs={"name": ("robots", "googlebot")})
    directives = f"{robots} " + " ".join(str(tag.get("content", "")) for tag in robots_tags)
    article = _best_editorial_container(soup)
    if (
        not allow_text
        or any(flag in directives.lower() for flag in ("noarchive", "nosnippet", "noindex"))
        or soup.select_one(
            "[data-paywall], .paywall, [itemprop=isAccessibleForFree][content=false]"
        )
    ):
        return Article(title[:500] or None, None, date)
    if article is None:
        # Several official sites use conventional named content containers
        # instead of semantic <main>/<article>. Extract only from recognizable
        # editorial sections; never treat the entire document as evidence.
        body = _extract_bip_sct(soup) if _is_bip_sct_page(source_url) else None
        if body is None:
            body = _extract_named_content(soup)
        return Article(title[:500] or None, body, date)
    for tag in article.select("script,style,nav,aside,footer,form,iframe,[data-nosnippet]"):
        tag.decompose()
    body = _editorial_blocks(article)
    return Article(title[:500] or None, body, date)


def _best_editorial_container(soup: BeautifulSoup) -> Tag | None:
    """Pick the substantive editorial body, not a short article preview.

    A short <article> may be only the teaser even when it has an article-body
    class; compare candidate text lengths before trusting the first selector.
    Never fall back to unrestricted <body> content.
    """
    selectors = (
        "[itemprop='articleBody']", "[data-article-body]", ".article-body",
        ".article__body", ".article-content", ".article-text",
        ".entry-content", ".post-content", "article", "main",
    )
    candidates: list[tuple[Tag, int]] = []
    seen: set[int] = set()
    for selector in selectors:
        for node in soup.select(selector):
            if not isinstance(node, Tag) or node.name in ("nav", "aside", "footer"):
                continue
            if id(node) in seen:
                continue
            seen.add(id(node))
            body = _editorial_blocks(node)
            if body:
                candidates.append((node, len(body)))
    if not candidates:
        return None
    # Prefer the richest candidate. A real article often has several hundred
    # characters while a teaser's short introductory paragraph is about 200.
    # Favor precise article-body markup if size is close to the richest one.
    richest = max(size for _, size in candidates)
    for node, size in candidates:
        if size >= max(100, int(richest * 0.9)):
            return node
    return max(candidates, key=lambda item: item[1])[0]


def _editorial_blocks(container: Tag) -> str | None:
    """Extract paragraphs and lists in reading order without duplicating nested text."""
    parts: list[str] = []
    for node in container.find_all(("p", "li")):
        if node.name == "li" and node.find("li"):
            continue
        if node.name == "p" and node.find_parent("li"):
            # Parent <li> contributes a complete single bullet.
            continue
        if any(parent.name in ("nav", "aside", "footer", "form", "header")
               for parent in node.parents if parent is not container):
            continue
        wording = " ".join(node.get_text(" ", strip=True).split())
        if wording and (not parts or parts[-1] != wording):
            parts.append(wording)
        if len(parts) >= 160:
            break
    return "\n\n".join(parts)[:50_000] or None


def _extract_named_content(soup: BeautifulSoup) -> str | None:
    """Conservative fallback for official-style named editorial containers.

    A recognizable container, a heading, and substantive paragraphs are
    required. Listing pages, map shells, footers and scripts stay metadata-only.
    The ingestion layer must already have approved this URL's text policy.
    """
    selectors = (
        "[role='main']", "[itemprop='articleBody']", ".article-content",
        ".entry-content", ".page-content", ".content-body",
        "#main-content", "#mainContent", "#content", ".text-content",
    )
    for selector in selectors:
        container = soup.select_one(selector)
        if container is None or container.name in ("nav", "aside", "footer", "form"):
            continue
        if not container.find(("h1", "h2", "h3")):
            continue
        for node in container.select(
            "script,style,nav,aside,footer,form,iframe,button,"
            "[data-nosnippet],[aria-hidden=true],.breadcrumb,.breadcrumbs"
        ):
            node.decompose()
        body = _editorial_blocks(container)
        blocks = body.split("\n\n") if body else []
        # Requiring at least two substantial pieces avoids navigation-only pages
        # and interactive map shells that return HTTP 200 without usable data.
        if len(blocks) >= 2 and len(body) >= 160:
            return body
    return None


def _is_bip_sct_page(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname == "www.bip.krakow.pl"
        and parts.path in ("", "/")
        and parse_qs(parts.query).get("dok_id") == ["226666"]
    )


def _extract_bip_sct(soup: BeautifulSoup) -> str | None:
    """Conservatively extract SCT sections, not the BIP menus or footer.

    This document has an H2 identifying its editorial content but no semantic
    <main> container. We walk only text blocks following that heading until
    the BIP metadata/footer boundary, without using arbitrary div text.
    """
    heading = next(
        (
            node for node in soup.find_all(("h2", "h3"))
            if "informacje dotyczące wprowadzenia strefy czystego transportu"
            in " ".join(node.get_text(" ", strip=True).casefold().split())
            and not any(parent.name in ("nav", "aside", "footer") for parent in node.parents)
        ),
        None,
    )
    if heading is None:
        return None

    content = []
    for node in heading.next_elements:
        if not isinstance(node, Tag):
            continue
        if node.name == "footer" or (
            node.name in ("a", "button")
            and "pokaż metkę" in node.get_text(" ", strip=True).casefold()
        ):
            break
        if node.name not in ("p", "li", "h2", "h3", "h4"):
            continue
        if node.name == "li" and node.find("li"):
            continue  # Do not repeat the nested paragraph or list entries.
        if any(
            parent.name in ("header", "nav", "aside", "footer", "form")
            for parent in node.parents
        ):
            continue
        if node.has_attr("data-nosnippet") or any(
            parent.has_attr("data-nosnippet") for parent in node.parents
        ):
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if text and (not content or text != content[-1]):
            content.append(text)
        if len(content) >= 250:
            break
    body = "\n\n".join(content)
    # Fail closed if markup changes and we no longer recognize the document.
    if (
        "strefa czystego transportu" not in body.casefold()
        or "obowiązuje" not in body.casefold()
    ):
        return None
    return body[:50_000] or None


def _published_at(value: str) -> datetime | None:
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        return None
    return date.astimezone(UTC) if date.tzinfo else None
