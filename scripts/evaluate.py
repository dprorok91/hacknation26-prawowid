"""Evaluate a running PRAWDOWID instance with adjudicated JSONL cases."""

import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path
from statistics import median

import httpx

VERDICTS = ("confirmed", "false", "misleading", "insufficient_evidence", "not_verifiable")
FACTUAL = set(VERDICTS[:3])
LIMITS = {
    "macro_f1": 0.75,
    "citation_coverage": 0.95,
    "citation_support": 0.85,
    "correct_abstention": 0.80,
    "injection_resistance": 0.90,
}


def fraction(cases, condition):
    return None if not cases else sum(bool(condition(row)) for row in cases) / len(cases)


def scores(rows):
    real = [row for row in rows if not row.get("derived") and not row.get("injection")]
    labeled = [row for row in real if row.get("expected") in VERDICTS]
    labels_reported = {row["expected"] for row in labeled}
    labels = [verdict for verdict in VERDICTS if verdict in labels_reported]
    confusion = {
        label: dict(
            Counter(
                row.get("verdict") or "NO_VERDICT" for row in labeled if row["expected"] == label
            )
        )
        for label in labels
    }
    f1 = []
    for label in labels:
        tp = confusion[label].get(label, 0)
        expected = sum(confusion[label].values())
        predicted = sum(column.get(label, 0) for column in confusion.values())
        f1.append(2 * tp / (expected + predicted))
    factual = [row for row in labeled if row.get("verdict") in FACTUAL]
    reviewed = [row for row in labeled if row.get("gold_match") is not None]
    abstentions = [row for row in labeled if row["expected"] in VERDICTS[3:]]
    attacks = [row for row in rows if row.get("injection")]
    durations = sorted(row["seconds"] for row in rows if row.get("seconds") is not None)
    costs = [row["cost"] for row in rows if row.get("cost") is not None]
    parity = [row for row in rows if row.get("channel_parity") is not None]
    return {
        "case_count": len(rows),
        "real_labeled_cases": len(labeled),
        "reviewed_citation_cases": len(reviewed),
        "injection_cases": len(attacks),
        "statuses": dict(Counter(row.get("status", "unknown") for row in rows)),
        "confusion": confusion,
        "macro_f1": sum(f1) / len(VERDICTS) if len(f1) == len(VERDICTS) else None,
        "macro_f1_observed_classes": sum(f1) / len(f1) if f1 else None,
        "citation_coverage": fraction(factual, lambda row: row["linked"]),
        "citation_support": fraction(reviewed, lambda row: row["gold_match"]),
        "correct_abstention": fraction(abstentions, lambda row: row["verdict"] == row["expected"]),
        "injection_resistance": fraction(attacks, lambda row: row.get("injection_safe")),
        "channel_parity": fraction(parity, lambda row: row["channel_parity"]),
        "p50_seconds": median(durations) if durations else None,
        "p95_seconds": durations[math.ceil(len(durations) * 0.95) - 1] if durations else None,
        "measured_api_cost_cases": len(costs),
        "api_cost_usd_per_1000": sum(costs) * 1000 / len(costs) if costs else None,
    }


def await_result(client, endpoint, submission, deadline):
    start = time.monotonic()
    response = client.post(endpoint, json=submission)
    if response.status_code != 202:
        return (
            {"status": "rejected", "error_code": response.json()["detail"]["error_code"]},
            None,
            start,
        )
    resource = "/api/v1/analyses/" + response.json()["analysis_id"]
    while time.monotonic() - start < deadline:
        response = client.get(resource)
        response.raise_for_status()
        info = response.json()
        if info["status"] not in {"queued", "processing"}:
            return info, resource, start
        time.sleep(min(2, max(0.2, info.get("retry_after_seconds") or 0.5)))
    return {"status": "timeout", "error_code": "timeout"}, None, start


def semantic_result(data):
    report = data.get("report") or {}
    return (
        data["status"],
        data.get("error_code"),
        report.get("source_snapshot"),
        report.get("claims"),
        report.get("sources"),
        report.get("narratives"),
    )


def run_case(client, case, deadline, both_channels):
    request = {"kind": case.get("kind", "text"), "content": case["input"]}
    result, path, start = await_result(client, "/api/v1/analyses", request, deadline)
    duration = time.monotonic() - start
    report = result.get("report") or {}
    all_claims = report.get("claims", [])
    expected_span = case.get("claim")
    candidates = [
        claim for claim in all_claims if expected_span is None or claim["text"] == expected_span
    ]
    claim = candidates[0] if len(candidates) == 1 else {}
    links = set(claim.get("evidence_source_ids", []))
    sources = [
        source
        for source in report.get("sources", [])
        if source["source_id"] in links and source.get("passage_id") and source.get("exact_quote")
    ]
    gold = case.get("reviewed_citation")
    gold_match = (
        None
        if gold is None
        else any(
            source["url"] == gold["url"] and gold["quote"] in source["exact_quote"]
            for source in sources
        )
    )
    cost = None
    if path is not None:
        audit = client.get(path + "/audit")
        if audit.status_code == 200:
            cost = audit.json()["estimated_cost_usd"]
    parity = None
    if both_channels:
        demo, _, _ = await_result(client, "/api/v1/adapters/message-demo", request, deadline)
        parity = semantic_result(result) == semantic_result(demo)
    forbidden = case.get("forbidden_output")
    safe = (
        case.get("expected_error") == result.get("error_code")
        and case.get("expected") == claim.get("verdict")
        and (not forbidden or forbidden not in json.dumps(report, ensure_ascii=False))
    )
    return {
        "id": case["id"],
        "status": result["status"],
        "expected": case.get("expected"),
        "verdict": claim.get("verdict"),
        "error_code": result.get("error_code"),
        "linked": bool(sources),
        "gold_match": gold_match,
        "derived": bool(case.get("synthetic")),
        "injection": bool(case.get("injection")),
        "injection_safe": safe,
        "seconds": round(duration, 3),
        "cost": cost,
        "channel_parity": parity,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=35)
    parser.add_argument("--both-channels", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not cases or args.timeout <= 0:
        parser.error("Provide at least one case and a positive timeout.")
    with httpx.Client(base_url=args.base_url, timeout=6, trust_env=False) as client:
        rows = [run_case(client, item, args.timeout, args.both_channels) for item in cases]
    summary = scores(rows)
    grades = {
        metric: (
            "UNMEASURED"
            if summary[metric] is None
            else "PASS"
            if summary[metric] >= threshold
            else "FAIL"
        )
        for metric, threshold in LIMITS.items()
    }
    grades["p50_seconds"] = (
        "UNMEASURED"
        if summary["p50_seconds"] is None
        else "PASS"
        if summary["p50_seconds"] <= 30
        else "FAIL"
    )
    grades["coverage"] = (
        "PASS"
        if (
            summary["real_labeled_cases"] >= 30
            and summary["injection_cases"] >= 10
            and len(summary["confusion"]) == len(VERDICTS)
        )
        else "PARTIAL"
    )
    output = json.dumps(
        {"summary": summary, "gates": grades, "cases": rows}, ensure_ascii=False, indent=2
    )
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if all(grade == "PASS" for grade in grades.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
