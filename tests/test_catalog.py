import json
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures"
records = run_path(str(ROOT / "scripts/catalog.py"))["catalog_records"]


def test_catalog_meets_public_metadata_thresholds_without_approving_text():
    entries = list(records(FIXTURES / "source_records.jsonl"))
    assert len({article.url for _, article in entries}) >= 100
    assert len({entry["domain"] for entry, _ in entries}) >= 10
    assert len({entry["source_type"] for entry, _ in entries}) >= 2
    assert all(article.body is None and entry["policy_url"] for entry, article in entries)


def test_reviewed_metadata_cases_reference_verified_source_records():
    source_urls = {
        json.loads(line)["url"]
        for line in (FIXTURES / "source_records.jsonl").read_text(encoding="utf-8").splitlines()
    }
    cases = [
        json.loads(line)
        for line in (FIXTURES / "reviewed_cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len({case["id"] for case in cases}) == 30
    assert all(set(case["source_urls"]) <= source_urls for case in cases)
    assert all(case["annotation_basis"] and case.get("reviewed_citation") is None for case in cases)
    assert {case["expected"] for case in cases} == {"confirmed", "false"}
