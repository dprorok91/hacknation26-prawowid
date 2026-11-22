"""Safe non-invasive SQL selection and bounded candidate filtering."""

from pathlib import Path
from runpy import run_path

from sqlalchemy.dialects import postgresql

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/index_approved_sources.py"


def test_select_approved_metadata_only_query_has_license_guards():
    module = run_path(str(SCRIPT))
    query = str(module["candidates_statement"]().compile(dialect=postgresql.dialect()))
    assert "metadata_only" not in query  # bound parameter, not raw interpolation
    assert "approved_at IS NOT NULL" in query
    assert "license_label IS NOT NULL" in query
    assert "enabled IS true" in query


def test_category_filter_and_budget_cap_happen_after_filtering():
    module = run_path(str(SCRIPT))
    choose = module["choose_candidates"]
    rows = [
        ("https://example.org/story", "Other", "Other"),
        ("https://www.gov.pl/web/rcb/notice", "Gov", "RCB"),
        ("https://pap.pl/aktualnosci/a", "PAP", "PAP"),
        ("https://www.gov.pl/web/mswia/notice", "Gov2", "MSWiA"),
        ("https://www.gov.pl/web/rcb/notice", "Duplicate", "RCB"),
    ]
    result = choose(rows, category="official", max_items=2)
    assert [item[3] for item in result] == ["official", "official"]
    assert len({item[0] for item in result}) == 2
    assert choose(rows, category="fact_check", max_items=5) == []
