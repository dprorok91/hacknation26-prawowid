"""Controlled HTTP document fetching and content-version tracking."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

TRACKING_PARAMETERS = {
    "ceid",
    "fbclid",
    "gclid",
    "gl",
    "hl",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_name",
    "utm_source",
    "utm_term",
}
ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


class FetchRejected(RuntimeError):
    """Raised before or during fetch because the target is unsafe or unsupported."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True)
class FetchedDocument:
    canonical_url: str
    final_url: str
    content_hash: str
    content_version: int
    text: str
    title: str | None
    published_at: datetime | None
    fetched_at: datetime
    text_license: str = "unknown"
    metadata_only: bool = False
    retrieval_status: str = "ready"


class SafeFetcher:
    def __init__(
        self,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        timeout_seconds: float = 8.0,
        max_redirects: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver=None,
    ) -> None:
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.transport = transport
        self.resolver = resolver or getaddrinfo
        self._versions: dict[str, str] = {}

    async def fetch(self, url: str) -> FetchedDocument:
        canonical_url = canonicalize_url(url)
        current_url = canonical_url

        limits = httpx.Limits(max_connections=4)
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(
            limits=limits,
            timeout=timeout,
            transport=self.transport,
            follow_redirects=False,
            headers={"User-Agent": "PrawdoWid/0.1 evidence-fetch (+offline-verification)"},
        ) as client:
            for redirect_count in range(self.max_redirects + 1):
                await self._validate_url(current_url)
                response = await client.get(current_url)

                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchRejected("invalid_response", "Redirect has no Location header")
                    if redirect_count == self.max_redirects:
                        raise FetchRejected("redirect_limit", "Too many redirects")
                    current_url = canonicalize_url(urljoin(str(response.url), location))
                    continue

                if response.status_code >= 400:
                    raise FetchRejected("source_unavailable", f"HTTP status {response.status_code}")

                content_type = response.headers.get("content-type", "application/octet-stream")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchRejected("unsupported_media", f"Unsupported content type: {media_type}")

                content = await response.aread()
                if len(content) > self.max_bytes:
                    raise FetchRejected("size_limit", "Response exceeds fetch size limit")

                break
            else:
                raise FetchRejected("redirect_limit", "Too many redirects")

        encoding = response.encoding or "utf-8"
        try:
            body = content.decode(encoding, errors="replace")
        except LookupError:
            body = content.decode("utf-8", errors="replace")

        soup = BeautifulSoup(body, "lxml")
        for element in soup(["script", "style", "noscript", "iframe", "object"]):
            element.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        text = soup.get_text("\n", strip=True) if media_type != "text/plain" else body.strip()

        content_hash = hashlib.sha256(content).hexdigest()
        prior_hash = self._versions.get(canonical_url)
        version = 1 if prior_hash is None else (prior_hash != content_hash)
        if prior_hash is None:
            version = 1
        else:
            version = self._version_number(canonical_url, prior_hash) + (1 if prior_hash != content_hash else 0)

        if prior_hash != content_hash:
            self._versions[canonical_url] = content_hash

        fetched_at = datetime.now(UTC)
        return FetchedDocument(
            canonical_url=canonical_url,
            final_url=str(response.url),
            content_hash=content_hash,
            content_version=version,
            text=text,
            title=title,
            published_at=None,
            fetched_at=fetched_at,
            metadata_only=not bool(text),
        )

    def _version_number(self, canonical_url: str, _prior_hash: str) -> int:
        # Offsets are tracked externally for persistence; this fallback is a 1-based in-memory counter.
        return getattr(self, "_version_counts", {}).get(canonical_url, 1)

    async def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise FetchRejected("invalid_url", "Only HTTP(S) URLs are allowed")
        if parsed.username or parsed.password:
            raise FetchRejected("invalid_url", "URLs with credentials are not allowed")
        if parsed.port is not None and parsed.port not in {80, 443}:
            raise FetchRejected("invalid_url", "Only ports 80 and 443 are allowed")

        host = parsed.hostname
        if not host:
            raise FetchRejected("invalid_url", "URL has no host")

        try:
            literal = ipaddress.ip_address(host)
            addresses = [literal]
        except ValueError:
            try:
                records = await self._resolve(host)
                addresses = [ipaddress.ip_address(item[4][0]) for item in records]
            except socket.gaierror as exc:
                raise FetchRejected("source_unavailable", "DNS resolution failed") from exc

        if not addresses:
            raise FetchRejected("source_unavailable", "DNS resolution returned no addresses")
        for address in addresses:
            if not address.is_global:
                raise FetchRejected("ssrf_blocked", f"Blocked non-public address: {address}")

    async def _resolve(self, host: str) -> list[tuple]:
        loop = __import__("asyncio").get_running_loop()
        return await loop.run_in_executor(None, self.resolver, host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise FetchRejected("invalid_url", "Only HTTP(S) URLs are allowed")
    if parsed.username or parsed.password:
        raise FetchRejected("invalid_url", "URLs with credentials are not allowed")

    host = parsed.hostname
    if not host:
        raise FetchRejected("invalid_url", "URL has no host")

    normalized_host = host.lower().rstrip(".")
    try:
        normalized_host = ipaddress.ip_address(normalized_host).compressed
    except ValueError:
        normalized_host = normalized_host.encode("idna").decode("ascii")

    port = parsed.port
    if port is not None and port not in {80, 443}:
        raise FetchRejected("invalid_url", "Only ports 80 and 443 are allowed")
    netloc = normalized_host
    if port is not None and not ((parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"

    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMETERS
    ]
    query = urlencode(pairs, doseq=True)
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))


class InMemoryDocumentStore:
    """Small test/store abstraction demonstrating unique canonical URLs and changed revisions."""

    def __init__(self) -> None:
        self.documents: dict[str, FetchedDocument] = {}

    def upsert(self, document: FetchedDocument) -> tuple[FetchedDocument, bool]:
        prior = self.documents.get(document.canonical_url)
        if prior is None:
            self.documents[document.canonical_url] = document
            return document, True
        if prior.content_hash == document.content_hash:
            return prior, False
        revision = FetchedDocument(
            **{
                **document.__dict__,
                "content_version": prior.content_version + 1,
            }
        )
        self.documents[document.canonical_url] = revision
        return revision, True


def getaddrinfo(host: str, port: object, family: int, type: int) -> list[tuple]:
    return socket.getaddrinfo(host, port, family=family, type=type)
