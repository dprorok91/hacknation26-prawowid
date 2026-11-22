"""V33 demos must expose safe opt-in sharing and read-only measurement."""

from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def test_ui_has_no_externally_loaded_scripts_or_fonts():
    html = BeautifulSoup(
        (ROOT / "src/prawdowid/web/index.html").read_text(encoding="utf-8"),
        "html.parser",
    )
    assert html.select_one("meta[name=theme-color]")["content"] == "#070c12"
    assert not html.select("script:not([src])")
    assert not html.select("link[href^=https]")
    assert html.select_one("input#enable-web-search[checked]")
    assert html.select_one("button[data-example]")
    assert html.select_one("#analysis-progress[hidden]")


def test_share_script_refuses_unprotected_tunnel():
    content = (ROOT / "scripts/share_demo_v33.sh").read_text(encoding="utf-8")
    assert "--allowed-mail" in content
    assert "if (( $# < 1 ))" in content
    assert "http://127.0.0.1:8000" in content
    assert "--url http://127.0.0.1:8000" in content
    assert "0.0.0.0:8000" not in content


def test_measurement_script_is_opt_in_and_local_only():
    content = (ROOT / "scripts/measure_demo_v33.py").read_text(encoding="utf-8")
    assert "127.0.0.1:8000" in content
    assert 'CASES[:1]' in content
    assert '"/api/v1/analyses"' in content
    assert '"/audit"' in content or '/audit' in content


def test_text_search_defaults_on_and_url_mode_obeys_api_contract():
    javascript = (ROOT / "src/prawdowid/web/app.js").read_text(encoding="utf-8")
    assert 'web_search: kind === "text" && webSearch.checked' in javascript
    assert 'webSearchChoice.hidden = isUrl' in javascript

def test_hosted_blueprint_requires_auth_and_real_db():
    deploy = (ROOT / "docs/render_v33.example.yaml").read_text(encoding="utf-8")
    assert "DEMO_BASIC_PASSWORD" in deploy
    assert "DATABASE_URL" in deploy
    assert "OPENROUTER_API_KEY" in deploy
    start = (ROOT / "scripts/start_hosted_demo.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in start
    assert "prawdowid.worker" in start
    assert "uvicorn" in start
