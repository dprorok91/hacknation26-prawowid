"""Safely inspect links from official seed pages without modifying the corpus.

Only the 40 curated entry points are requested (robots.txt is respected).
Found links are NOT fetched, approved, or treated as evidence. Report ZIP is
written to Downloads without credentials, text content or API keys.
"""

import argparse
import asyncio
import json
import re
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.safe_web import FetchError, SafeWeb

SEEDS = Path(__file__).with_name("official_sources.json")
MAX_LINKS = 40
DIGIT_PATH = re.compile(r"(?:^|/)\d{2,}(?:/|$)")
AUDIT_SECRET_KEYS = re.compile(r"(?i)(?:^|[_-])(token|session|password|secret|api_key|key)(?:$|[_-])")


def classify_link(url: str) -> str:
    """Describe the URL shape, not its license, quality or evidentiary status."""
    parts = urlsplit(url)
    if parts.path.casefold().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
        return "download"
    if parts.fragment.startswith(("/", "!/")):
        return "client_route"
    if parts.query:
        return "query_parameters"
    if DIGIT_PATH.search(parts.path):
        return "numeric_path"
    return "path"


def find_links(html: bytes, base: str, *, max_links: int = MAX_LINKS) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(base).hostname
    collected: dict[str, dict[str, str]] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href", "")).strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:")):
            continue
        url = urljoin(base, href)
        if urlsplit(url).hostname != host:
            continue
        # Audit reports must not export credential-bearing URL parameters.
        if any(AUDIT_SECRET_KEYS.search(key) for key, _ in parse_qsl(urlsplit(url).query)):
            continue
        try:
            canonical_url(url)
        except ValueError:
            continue
        shape = classify_link(url)
        if shape == "path" and urlsplit(url).path == urlsplit(base).path:
            continue
        if shape == "path":
            continue  # Focus on unusual patterns, not the whole navigation tree.
        collected.setdefault(url, {"url": url, "shape": shape})
        if len(collected) >= max_links:
            break
    return list(collected.values())


async def scan(entry: dict, client: httpx.AsyncClient, sem: asyncio.Semaphore) -> dict:
    url = entry["url"]
    async with sem:
        try:
            page = await SafeWeb(client, max_bytes=750_000, timeout=12).read_article(url)
            links = find_links(page.content, page.url)
            return {
                "seed": url, "status": "ok", "final_url": page.url,
                "html_bytes": len(page.content), "links": links,
                "link_patterns": dict(Counter(link["shape"] for link in links)),
                "mode": entry["mode"],
            }
        except FetchError as exc:
            return {"seed": url, "status": exc.reason, "links": [], "mode": entry["mode"]}


async def run(workers: int) -> list[dict]:
    entries = json.loads(SEEDS.read_text(encoding="utf-8"))
    unique = {canonical_url(item["url"]): item for item in reversed(entries)}
    semaphore = asyncio.Semaphore(workers)
    async with httpx.AsyncClient(trust_env=False, timeout=12) as client:
        tasks = [scan(item, client, semaphore) for item in unique.values()]
        return await asyncio.gather(*tasks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    args = parser.parse_args()
    results = asyncio.run(run(args.workers))
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output = Path.home() / "Downloads" / f"prawowid_official_url_audit_{stamp}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "requested_http_pages": len(results),
        "successful": sum(row["status"] == "ok" for row in results),
        "links_found": sum(len(row["links"]) for row in results),
        "statuses": dict(Counter(row["status"] for row in results)),
        "note": "URLs only, not evidence. Client-side content and PDFs require separate adapters.",
    }
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
        archive.writestr("results.json", json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"AUDIT ZIP: {output}")


if __name__ == "__main__":
    main()
