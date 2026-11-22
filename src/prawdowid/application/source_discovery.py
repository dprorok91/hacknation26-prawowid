"""Conservative bounded link extraction from approved public listing pages.

Discovery supplies candidate metadata, not evidence or a license grant.
Do not allow arbitrary off-site links to become ingestion jobs.
"""

from dataclasses import dataclass
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup

from prawdowid.domain.models import DocumentCandidate
from prawdowid.domain.urls import canonical_url, parse_http_url

EXCLUDED_EXTENSIONS = (
    ".jpg", ".jpeg", ".gif", ".png", ".webp", ".svg", ".zip", ".rar",
    ".csv", ".xlsx", ".docx", ".pptx", ".mp3", ".mp4",
)
SKIP_PATH_PARTS = frozenset((
    "logowanie", "login", "rejestracja", "rejestruj", "privacy", "polityka-prywatnosci",
    "kontakt", "cookies", "regulamin", "search", "szukaj", "newsletter",
))
PRIVATE_QUERY_KEYS = frozenset((
    "token", "access_token", "refresh_token", "session", "sessionid", "sid",
    "api_key", "apikey", "password", "secret", "authorization", "auth",
))


@dataclass(frozen=True)
class CandidateLinks:
    articles: tuple[DocumentCandidate, ...]
    next_pages: tuple[str, ...]


def _safe_same_host(href: str, base: str) -> str | None:
    try:
        absolute = urljoin(base, href)
        parts = parse_http_url(absolute)
        if (parts.hostname or "").casefold() != (urlsplit(base).hostname or "").casefold():
            return None
        if any(key.casefold() in PRIVATE_QUERY_KEYS for key, _ in parse_qsl(parts.query)):
            return None
        return canonical_url(absolute)
    except ValueError:
        return None


def discover_links(html: bytes, base_url: str, *, max_links: int = 40) -> CandidateLinks:
    """Find likely articles/PDFs and next-page links; no network or DB access."""
    if not 1 <= max_links <= 100:
        raise ValueError("max_links must be between 1 and 100")
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("meta", attrs={"name": "robots", "content": lambda x: x and "nofollow" in x.lower()}):
        return CandidateLinks((), ())
    for node in soup.select("script,style,nav,footer,aside,form"):
        node.decompose()
    result: dict[str, DocumentCandidate] = {}
    next_pages = []
    for node in soup.find_all("a", href=True):
        url = _safe_same_host(str(node.get("href", "")), base_url)
        if url is None or url == canonical_url(base_url):
            continue
        parts = urlsplit(url)
        pathname = parts.path.lower().rstrip("/")
        if any(pathname.endswith(ext) for ext in EXCLUDED_EXTENSIONS):
            continue
        path_parts = tuple(part for part in pathname.strip("/").split("/") if part)
        if any(part in SKIP_PATH_PARTS for part in path_parts):
            continue
        label = " ".join(node.get_text(" ", strip=True).split())[:500]
        query_keys = {key.lower() for key, _ in parse_qsl(parts.query)}
        rel = tuple(str(value).casefold() for value in (node.get("rel") or ()))
        if (
            "next" in rel
            or ("page" in query_keys and len(label) < 12)
            or label.casefold() in ("następna", "dalej", "next", "›", ">")
        ):
            if url not in next_pages and len(next_pages) < 3:
                next_pages.append(url)
            continue
        is_pdf = pathname.endswith(".pdf")
        # Query-only and single-segment links are sometimes real articles;
        # accept them only if they have meaningful editorial anchor text.
        if (
            len(result) < max_links
            and (is_pdf or len(path_parts) >= 2 or bool(parts.query))
            and len(label) >= 12
        ):
            result.setdefault(
                url, DocumentCandidate(
                    canonical_url=url,
                    publisher=urlsplit(base_url).hostname or "",
                    headline=label,
                    published_at=None,
                    provider="approved_listing_discovery",
                )
            )
    return CandidateLinks(tuple(result.values()), tuple(next_pages))
