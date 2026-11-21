"""Collect citation metadata from a fixed set of Polish public feeds and APIs."""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import islice
from time import sleep
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
from bs4 import BeautifulSoup

from prawdowid.domain.urls import canonical_url

USER_AGENT = "PRAWDOWID-Metadata/0.1"
MAX_FEED_BYTES = 1_500_000
MAX_ROBOTS_BYTES = 512_000
MAX_CRAWL_DELAY = 8


@dataclass(frozen=True)
class Source:
    publisher: str
    source_type: str
    endpoint: str
    format: str
    policy_url: str
    policy_label: str
    record_hosts: tuple[str, ...] = ()


SOURCES = (
    Source(
        "Kancelaria Sejmu / Dziennik Ustaw",
        "primary",
        "https://api.sejm.gov.pl/eli/acts/DU/2025",
        "eli",
        "https://eli.gov.pl/ELI",
        "ELI legal-act metadata; no act text collected",
        ("eli.gov.pl", "api.sejm.gov.pl"),
    ),
    Source(
        "Narodowy Bank Polski",
        "primary",
        "https://api.nbp.pl/api/exchangerates/tables/a/last/14/",
        "nbp",
        "https://nbp.pl/statystyka-i-sprawozdawczosc/kursy/instrukcja-pobierania-kursow-walut/",
        "NBP published exchange-table identifiers and dates; exchange rates excluded",
    ),
    Source(
        "IMGW-PIB",
        "primary",
        "https://danepubliczne.imgw.pl/api/data/synop",
        "imgw",
        "https://danepubliczne.imgw.pl/en/regulations",
        "IMGW data regulations; station IDs and measurement times only",
    ),
    Source(
        "Główny Urząd Statystyczny",
        "primary",
        "https://stat.gov.pl/rss/pl/5438/8.xml",
        "rss",
        "https://bip.stat.gov.pl/kontakt/ponowne-wykorzystywanie-informacji-sektora-publicznego/",
        "GUS attribution and retrieval date; public feed metadata",
    ),
    Source(
        "Urząd Ochrony Konkurencji i Konsumentów",
        "primary",
        "https://uokik.gov.pl/feed",
        "rss",
        "https://uokik.gov.pl/public/index.php/bip/wnioskowanie-o-dostep-do-informacji-sektora-publicznego-w-celu-jej-ponownego-wykorzystywania",
        "UOKiK reuse terms require a link and retrieval date; public feed metadata",
    ),
    Source(
        "Najwyższa Izba Kontroli",
        "primary",
        "https://www.nik.gov.pl/rss/id,1.html",
        "rss",
        "https://www.nik.gov.pl/zasady/prawa-autorskie.html",
        "NIK publisher notice; public feed metadata",
    ),
    Source(
        "Stowarzyszenie Demagog",
        "factcheck",
        "https://demagog.org.pl/feed/sharefeed",
        "rss",
        "https://demagog.org.pl/zasady-udostepniania-tresci/",
        "Demagog noncommercial, unmodified reuse terms; public feed metadata only",
    ),
    Source(
        "Nauka o klimacie",
        "news",
        "https://naukaoklimacie.pl/feed",
        "rss",
        "https://naukaoklimacie.pl/o-nas",
        "Publisher lists CC BY-NC-ND 3.0 PL for article text unless marked otherwise; metadata only",
    ),
    Source(
        "Ministerstwo Klimatu i Środowiska",
        "primary",
        "https://www.gov.pl/web/klimat/wiadomosci",
        "gov",
        "https://www.gov.pl/web/gov/prawa-autorskie",
        "Gov.pl copyright and reuse notice; first-page headline metadata",
    ),
    Source(
        "Fundacja im. Stefana Batorego",
        "news",
        "https://www.batory.org.pl/feed/",
        "rss",
        "https://www.batory.org.pl/dla-mediow/",
        "Publisher's CC BY-NC-ND 4.0 notice; public feed metadata only",
    ),
)


class CollectionError(Exception):
    pass


def fetch(
    client: httpx.Client, url: str, size_limit: int, rules: RobotFileParser | None = None
) -> tuple[int, bytes]:
    origin = urlsplit(url).netloc
    for _ in range(3):
        if rules is not None and not rules.can_fetch(USER_AGENT, url):
            raise CollectionError("robots.txt disallows this feed")
        with client.stream("GET", url) as response:
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.netloc != origin or parts.username:
                    raise CollectionError("redirect outside the configured HTTPS origin")
                continue
            if response.status_code != 200:
                return response.status_code, b""
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise CollectionError("unexpected content encoding")
            content = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(content) + len(chunk) > size_limit:
                    raise CollectionError("response exceeds the size limit")
                content.extend(chunk)
            return 200, bytes(content)
    raise CollectionError("redirect limit exceeded")


def respect_robots(client: httpx.Client, url: str) -> RobotFileParser | None:
    status, content = fetch(client, urljoin(url, "/robots.txt"), MAX_ROBOTS_BYTES)
    if status in (404, 410):
        return
    if status != 200:
        raise CollectionError(f"robots.txt returned HTTP {status}")
    rules = RobotFileParser()
    rules.parse(content.decode("utf-8-sig").splitlines())
    if not rules.can_fetch(USER_AGENT, url):
        raise CollectionError("robots.txt disallows this feed")
    delay = rules.crawl_delay(USER_AGENT) or 0
    rate = rules.request_rate(USER_AGENT)
    if rate is not None and rate.requests > 0:
        delay = max(delay, rate.seconds / rate.requests)
    if delay > MAX_CRAWL_DELAY:
        raise CollectionError("publisher rate policy exceeds the collection time allowance")
    if delay:
        sleep(delay)
    return rules


def rss_entries(content: bytes):
    xml = content.decode("utf-8-sig")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        raise CollectionError("feed uses an unexpected XML declaration")
    feed = feedparser.parse(content)
    if not feed.version:
        raise CollectionError("unrecognized RSS or Atom feed")
    for item in feed.entries:
        published = item.get("published_parsed")
        yield {
            "url": item.get("link", ""),
            "title": item.get("title", ""),
            "published_at": datetime(*published[:6], tzinfo=UTC).isoformat() if published else None,
        }


def ministry_entries(content: bytes, endpoint: str):
    soup = BeautifulSoup(content, "html.parser")
    for link in soup.select(".title > a[href]"):
        href = urljoin(endpoint, link["href"])
        if not urlsplit(href).path.startswith("/web/klimat/"):
            continue
        date_element = link.parent.parent.select_one(".date")
        published = None
        if date_element:
            published = datetime.strptime(date_element.get_text(strip=True), "%d.%m.%Y").date()
        yield {
            "url": href,
            "title": link.get_text(" ", strip=True),
            "published_at": published.isoformat() if published else None,
        }


def api_entries(content: bytes, source: Source):
    value = json.loads(content)
    if source.format == "eli":
        for item in value["items"]:
            identifier = item.get("ELI")
            if not isinstance(identifier, str) or not re.fullmatch(
                r"DU/[0-9]{4}/[0-9]+", identifier
            ):
                continue
            yield {
                "url": f"https://eli.gov.pl/eli/{identifier}/ogl",
                "title": item["title"],
                "published_at": item.get("promulgation"),
            }
    elif source.format == "nbp":
        for item in value:
            published = date.fromisoformat(item["effectiveDate"]).isoformat()
            yield {
                "url": f"https://api.nbp.pl/api/exchangerates/tables/a/{published}/",
                "title": f"Tabela kursów średnich NBP {item['no']}",
                "published_at": published,
            }
    elif source.format == "imgw":
        for item in value:
            station_id = str(item["id_stacji"])
            if not station_id.isascii() or not station_id.isdigit():
                continue
            observed = date.fromisoformat(item["data_pomiaru"])
            hour = int(item["godzina_pomiaru"])
            if not 0 <= hour <= 23:
                continue
            yield {
                "url": f"https://danepubliczne.imgw.pl/api/data/synop/id/{station_id}",
                "title": f"Obserwacja synoptyczna IMGW-PIB: {item['stacja']}",
                "published_at": None,
                "observed_at": f"{observed.isoformat()}T{hour:02d}:00:00",
            }


def collect(client: httpx.Client, source: Source, limit: int) -> list[dict]:
    rules = respect_robots(client, source.endpoint)
    status, content = fetch(client, source.endpoint, MAX_FEED_BYTES, rules)
    if status != 200:
        raise CollectionError(f"source endpoint returned HTTP {status}")
    retrieved = datetime.now(UTC).isoformat(timespec="seconds")
    if source.format == "rss":
        entries = rss_entries(content)
    elif source.format == "gov":
        entries = ministry_entries(content, source.endpoint)
    else:
        entries = api_entries(content, source)

    records = []
    known = set()
    allowed_hosts = source.record_hosts or (urlsplit(source.endpoint).hostname,)
    for entry in islice(entries, limit * 3):
        try:
            url = canonical_url(entry["url"].strip())
        except ValueError:
            continue
        parts = urlsplit(url)
        title = " ".join(entry["title"].split())
        if (
            parts.scheme != "https"
            or parts.hostname not in allowed_hosts
            or not title
            or url in known
        ):
            continue
        records.append(
            {
                **entry,
                "url": url,
                "title": title,
                "domain": parts.hostname.removeprefix("www."),
                "publisher": source.publisher,
                "source_type": source.source_type,
                "retrieved_at": retrieved,
                "policy_url": source.policy_url,
                "policy_label": source.policy_label,
                "discovery_url": source.endpoint,
                "metadata_only": True,
            }
        )
        known.add(url)
        if len(records) == limit:
            break
    if not records:
        raise CollectionError("no valid metadata records found")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-per-source", type=int, choices=range(1, 21), default=12)
    args = parser.parse_args()
    unique_records = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        timeout=8,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for source in SOURCES:
            try:
                records = collect(client, source, args.limit_per_source)
            except CollectionError as exc:
                print(f"{source.publisher}: {exc}", file=sys.stderr)
                continue
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                print(f"{source.publisher}: {type(exc).__name__}", file=sys.stderr)
                continue
            for row in records:
                unique_records.setdefault(row["url"], row)
            print(f"{source.publisher}: {len(records)} records", file=sys.stderr)
    for row in unique_records.values():
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    domains = Counter(row["domain"] for row in unique_records.values())
    source_types = Counter(row["source_type"] for row in unique_records.values())
    print(
        f"{len(unique_records)} records, {len(domains)} domains, {dict(source_types)}",
        file=sys.stderr,
    )
    return 0 if len(unique_records) >= 100 and len(domains) >= 10 and len(source_types) >= 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
