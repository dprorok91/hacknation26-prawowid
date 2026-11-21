from pathlib import Path
from runpy import run_path

scores = run_path(str(Path(__file__).parents[1] / "scripts" / "evaluate.py"))["scores"]


def test_unmeasured_citation_support_is_unknown_and_outages_are_counted():
    result = scores(
        [
            {
                "expected": "confirmed",
                "verdict": "confirmed",
                "linked": True,
                "seconds": 2,
                "derived": False,
                "injection": False,
                "gold_match": None,
                "status": "completed",
            },
            {
                "expected": "confirmed",
                "verdict": None,
                "linked": False,
                "seconds": 4,
                "derived": False,
                "injection": False,
                "gold_match": None,
                "status": "failed",
            },
        ]
    )
    assert result["citation_coverage"] == 1
    assert result["citation_support"] is None
    assert result["macro_f1"] is None
    assert result["macro_f1_observed_classes"] == 2 / 3
    assert result["confusion"]["confirmed"]["NO_VERDICT"] == 1
    assert result["p50_seconds"] == 3


def test_synthetic_cases_do_not_enter_real_verdict_metric():
    result = scores(
        [
            {
                "expected": "confirmed",
                "verdict": "confirmed",
                "linked": True,
                "seconds": 2,
                "derived": True,
                "injection": False,
                "gold_match": None,
                "status": "completed",
            },
        ]
    )
    assert result["macro_f1"] is None
