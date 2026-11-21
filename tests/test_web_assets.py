"""Checks for the dependency-free report UI's local assets and accessible entry form."""

from pathlib import Path

from bs4 import BeautifulSoup

WEB_ROOT = Path(__file__).resolve().parents[1] / "src" / "prawdowid" / "web"


def test_form_options_and_labels_match_analysis_input_contract():
    page = BeautifulSoup((WEB_ROOT / "index.html").read_text(encoding="utf-8"), "html.parser")
    form = page.select_one("#analysis-form")
    assert page.html["lang"] == "pl"
    assert {radio["value"] for radio in form.select("[name=kind]")} == {"text", "url"}
    assert form.select_one("[type=radio][checked]")["value"] == "text"
    assert form.select_one("input#content-url")["type"] == "url"
    for field_id in ("content-text", "content-url"):
        assert form.select_one(f"label[for={field_id}]").get_text(strip=True)
        assert form.select_one(f"#{field_id}")["maxlength"] == "20000"
    assert page.select_one("#analysis-status")["role"] == "status"
    assert page.select_one("#report")["aria-labelledby"] == "report-title"


def test_page_loads_only_local_owned_assets():
    page = BeautifulSoup((WEB_ROOT / "index.html").read_text(encoding="utf-8"), "html.parser")
    paths = [item["href"] for item in page.select("link[rel=stylesheet]")]
    paths.extend(item["src"] for item in page.select("script[src]"))
    assert sorted(paths) == ["/assets/app.js", "/assets/styles.css"]
    assert all((WEB_ROOT / Path(path).name).is_file() for path in paths)
    assert not page.select("script:not([src])")


def test_analysis_delete_is_hidden_and_confirmation_has_cancel_default():
    page = BeautifulSoup((WEB_ROOT / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert page.select_one("button#delete-analysis[hidden]")
    dialog = page.select_one("dialog#delete-dialog")
    assert dialog.select_one(f"#{dialog['aria-labelledby']}")
    assert dialog.select_one("form[method=dialog] button[value=cancel][autofocus]")
    assert dialog.select_one("form[method=dialog] button[value=delete]")
