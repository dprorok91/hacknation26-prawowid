"""The original isolated V31.1 benchmark is retained as production regression data."""

import json
from pathlib import Path

import pytest

from prawdowid.application.evidence_selection import document_role, select

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "v31_retrieval_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_v31_isolated_benchmark_regression(case):
    picked, _trace = select(case["text"], case["claim"])
    ids = {p.paragraph_id for p in picked}
    assert set(case["gold"]) <= ids
    assert not (ids & set(case["distractors"]))
    assert all(case["text"][p.start:p.end] == p.text for p in picked)
    assert all(document_role(p.text) == "editorial" for p in picked)
