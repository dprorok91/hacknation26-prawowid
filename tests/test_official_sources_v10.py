"""Regression cases from 40 official source URLs and their real-world URL patterns."""

import json
from pathlib import Path
from runpy import run_path

import pytest

from prawdowid.application.evidence import _has_claim_anchors
from prawdowid.domain.urls import canonical_url, parse_http_url

SOURCES = Path(__file__).parents[1] / "scripts" / "official_sources.json"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bip.krakow.pl/?dok_id=226666",
        "https://powietrze.gios.gov.pl/pjp/current?lang=pl",
        "https://powietrze.gios.gov.pl/pjp/content/api?theme=default",
        "https://powietrze.gios.gov.pl/pjp/content/show/1005904?lang=pl",
        "https://drogi.gddkia.gov.pl/informacje-drogowe/lista-utrudnien?woj=mazowieckie",
        "https://drogi.gddkia.gov.pl/informacje-drogowe/lista-utrudnien?rodzaj=I13",
        "https://drogi.gddkia.gov.pl/mapy/mapa-informacji-drogowej?situation=NTIkNTMuNjUw",
        "https://drogi.gddkia.gov.pl/mapy/mapa-informacji-drogowej?lat=51.84&lng=19.65",
        "https://ulc.gov.pl/prawa-pasazera/poradnik-dla-pasazerow?start=10",
        "https://ulc.gov.pl/?Itemid=138",
        "https://ulc.gov.pl/_download/kopp/Prawa-pasazerow-ulotka-do-druku.pdf",
        "https://www.gov.pl/web/gov/ostrzezenia#/_/mazowieckie/wszystkie",
        "https://example.org/report?doc=8128&view=full&language=pl",
        "https://example.org/report?reference=PL-2026-10-21&articleId=343",
    ],
)
def test_url_qualification_is_not_document_id_allowlist(url):
    assert parse_http_url(url).scheme == "https"


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.org/doc?id=1",
        "file:///tmp/doc?id=1",
        "https://example.org/doc\\path",
    ],
)
def test_invalid_document_url_syntax_is_rejected(url):
    with pytest.raises(ValueError):
        parse_http_url(url)


def test_all_supplied_urls_are_present_and_deduplicated_by_http_resource():
    rows = json.loads(SOURCES.read_text(encoding="utf-8"))
    assert len(rows) == 40
    assert all(parse_http_url(row["url"]).scheme == "https" for row in rows)
    canonical = {canonical_url(row["url"]) for row in rows}
    assert len(canonical) == 39  # Hash-only Mazowieckie view shares the same HTTP URL.
    assert all(row["mode"] for row in rows)


def test_database_import_preflight_is_read_only_and_uses_safe_urls():
    mod = run_path(str(SOURCES.with_name("register_official_sources.py")))
    entries = mod["official_seeds"](SOURCES)
    assert len(entries) == 40
    assert len({entry["canonical"] for entry in entries}) == 39


@pytest.mark.parametrize(
    ("claim", "quote", "expected"),
    [
        (
            "Strefa Czystego Transportu zaczęła obowiązywać 1 stycznia 2026 roku.",
            "SCT obowiązuje od 01.01.2026 r.",
            True,
        ),
        (
            "Strefa Czystego Transportu zaczęła obowiązywać 1 stycznia 2026 roku.",
            "SCT obowiązuje od 2026-01-01.",
            True,
        ),
        (
            "Strefa Czystego Transportu zaczęła obowiązywać 1 stycznia 2026 roku.",
            "SCT obowiązuje od 02.01.2026 r.",
            False,
        ),
        (
            "Strefa Czystego Transportu zaczęła obowiązywać 1 stycznia 2026 roku.",
            "Inna ustawa weszła w życie 01.01.2026 r.",
            False,
        ),
    ],
)
def test_sct_acronym_and_date_format_anchor_normalization(claim, quote, expected):
    assert _has_claim_anchors(claim, quote) is expected


def test_named_html_content_fallback_extracts_editorial_text():
    from prawdowid.infrastructure.safe_web import extract_article

    html = """
    <html><title>Komunikat służby</title><nav><p>Menu informacje</p></nav>
    <div id="content"><h1>Komunikat urzędowy</h1>
      <p>W dniu 22 listopada 2026 r. opublikowano komunikat o zdarzeniu na obszarze kraju.</p>
      <p>Informacje dotyczą zdarzenia oraz zawierają wskazówki dla mieszkańców regionu.</p>
      <aside><p>Reklama i boczne menu nie powinny być dowodem.</p></aside>
    </div>
    </html>
    """
    article = extract_article(html.encode(), allow_text=True, source_url="https://gov.pl/doc?id=5")
    assert "W dniu 22 listopada" in (article.body or "")
    assert "boczne menu" not in (article.body or "")


def test_named_content_fallback_does_not_grab_menus_or_js_shells():
    from prawdowid.infrastructure.safe_web import extract_article

    html = """
    <html><title>Mapa</title><div id="content"><h1>Mapa zdarzeń</h1>
    <nav><p>Filtry województwa i wybór mapy</p></nav>
    <div id="map">Trwa ładowanie danych</div>
    </div></html>
    """
    assert extract_article(html.encode(), allow_text=True).body is None
    assert extract_article(html.encode(), allow_text=False).body is None
    assert extract_article(html.encode(), allow_text=True, robots="nosnippet").body is None


def test_scanner_collects_unusual_links_without_secrets():
    module = run_path(str(SOURCES.with_name("audit_official_url_patterns.py")))
    html = b"""<html><body>
      <a href="/download/report.pdf">PDF</a>
      <a href="/current?lang=pl">Lang</a>
      <a href="/map?situation=AAAA">Map</a>
      <a href="/alert/2026/218/notice/">Alert</a>
      <a href="/viewer#/_/mazowieckie">Client route</a>
      <a href="/doc?access_token=secret">Private</a>
      <a href="http://127.0.0.1/admin">Local</a>
      <a href="https://other.gov.pl/doc?id=1">Other domain</a>
    </body></html>"""
    results = module["find_links"](html, "https://gov.pl/start")
    assert {row["shape"] for row in results} == {
        "download", "query_parameters", "numeric_path", "client_route"
    }
    assert len(results) == 5
    assert all("access_token" not in row["url"] for row in results)
