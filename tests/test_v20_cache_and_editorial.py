"""V20 regression: resubmitting a URL must not reuse the V18/V19 report."""

from pathlib import Path

from prawdowid.infrastructure.analysis_store import PIPELINE_VERSION
from prawdowid.infrastructure.safe_web import extract_article
from prawdowid.main import WEB_HEADERS


def test_v20_cache_version_invalidates_previous_url_analyses():
    assert PIPELINE_VERSION == "v20"


def test_new_homepage_forces_reload_of_js_and_prevents_html_staleness():
    index = (Path(__file__).resolve().parents[1] / "src/prawdowid/web/index.html").read_text()
    assert 'src="/assets/app.js?v=20"' in index
    assert WEB_HEADERS["Cache-Control"] == "no-store"


def test_short_article_teaser_does_not_hide_full_editorial_main():
    """This mirrors an article whose summary is wrapped in <article>."""
    lead = (
        "Miasto proponuje zmniejszenie Strefy Czystego Transportu z ok. "
        "196 km² do ok. 0,99 km² w centrum Krakowa. "
        "Dalsze zmiany opisano w oddzielnej części strony."
    )
    html = f"""<html><body><article><p>{lead}</p></article><main>
    <p>Strefa Czystego Transportu ma się skurczyć z 196 do 0,99 km².</p>
    <h2>Zasady zwolnień</h2>
    <p>Rozszerzenie zwolnień obejmie osoby poruszające się autem służbowym.</p>
    <ul><li>Projekt zakłada zniesienie nalepek identyfikacyjnych na szybach.</li>
    <li>Nowe zasady obejmą także najem samochodów.</li></ul>
    <h2>Opłaty</h2>
    <p>Wysokość opłat nie zmieni się w obecnym projekcie uchwały.</p>
    <p>W 2024 roku odnotowano przekroczenie normy dwutlenku azotu.</p>
    <p>Uchwała nie weszła jeszcze w życie i wymaga konsultacji.</p>
    </main></body></html>""".encode()
    parsed = extract_article(html, allow_text=True)
    assert parsed.body
    assert "Rozszerzenie zwolnień" in parsed.body
    assert "najem samochodów" in parsed.body
    assert "wymaga konsultacji" in parsed.body
    assert len(parsed.body) > 400


def test_preferred_real_article_container_beats_related_links_inside_main():
    html = b"""<main><article><div itemprop="articleBody">
    <p>The city has proposed a new transport zone and several resident exemptions.</p>
    <p>Drivers of company vehicles may also qualify for an exemption.</p>
    </div></article><aside><p>Other articles about unrelated topics and ads.</p></aside></main>"""
    parsed = extract_article(html, allow_text=True)
    assert "company vehicles" in parsed.body
    assert "unrelated topics" not in parsed.body
