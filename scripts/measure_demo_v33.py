"""Run a small, opt-in LOCAL API benchmark and summarize latency and model cost.

Usage:
  uv run --locked python scripts/measure_demo_v33.py --out ~/Downloads/v33-demo.json
  uv run --locked python scripts/measure_demo_v33.py --all --out ~/Downloads/v33-all.json

Default makes ONE paid live query. --all makes FIVE. Never calls a remote host.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
CASES = [
    ("sct", "text", "Władze Krakowa zaproponowały zmniejszenie SCT."),
    (
        "road_memorial", "text",
        ("Światowy Dzień Pamięci Ofiar Wypadków Drogowych obchodzony jest w trzecią "
        "niedzielę listopada."),
    ),
    (
        "opinion", "text",
        "Strefy czystego transportu są niesprawiedliwe wobec kierowców.",
    ),
    (
        "complex", "text",
        ("Władze Krakowa przygotowały projekt zmniejszenia strefy czystego transportu, "
        "ale nowe granice obowiązują dopiero po przyjęciu i ogłoszeniu uchwały."),
    ),
    (
        "official_article", "url",
        ("https://www.gov.pl/web/infrastruktura/swiatowy-dzien-pamieci-ofiar-wypadkow-drogowych---"
        "dzialamy-na-rzecz-poprawy-bezpieczenstwa-na-drogach"),
    ),
]


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def measure(case: tuple[str, str, str], timeout_seconds: int) -> dict:
    name, kind, content = case
    begun = time.perf_counter()
    entry = {
        "name": name,
        "status": "blocked",
        "latency_seconds": None,
        "cost_usd": None,
        "tokens_in": None,
        "tokens_out": None,
        "citations": 0,
        "claims": 0,
    }
    try:
        response = request_json(
            "POST", "/api/v1/analyses",
            {"kind": kind, "content": content, "language": "pl", "web_search": kind == "text"},
        )
        analysis_id = response["analysis_id"]
        deadline = time.monotonic() + timeout_seconds
        while response["status"] in ("queued", "processing"):
            if time.monotonic() >= deadline:
                entry["status"] = "timeout"
                return entry
            time.sleep(2)
            response = request_json("GET", "/api/v1/analyses/" + analysis_id)
        entry["status"] = response["status"]
        report = response.get("report") or {}
        entry["claims"] = len(report.get("claims") or [])
        entry["citations"] = len(report.get("sources") or [])
        try:
            audit = request_json("GET", f"/api/v1/analyses/{analysis_id}/audit")
            entry["cost_usd"] = audit.get("estimated_cost_usd")
            entry["tokens_in"] = audit.get("tokens_in")
            entry["tokens_out"] = audit.get("tokens_out")
        except (urllib.error.URLError, ValueError, KeyError, TimeoutError):
            pass
    except (urllib.error.URLError, ValueError, KeyError, TimeoutError) as error:
        entry["status"] = "error"
        entry["error_type"] = type(error).__name__
    finally:
        entry["latency_seconds"] = round(time.perf_counter() - begun, 2)
    return entry


def summarize(results: list[dict]) -> dict:
    completed = [row for row in results if row["status"] == "completed"]
    durations = [row["latency_seconds"] for row in completed]
    costs = [row["cost_usd"] for row in completed if row["cost_usd"] is not None]
    return {
        "completed": len(completed),
        "total": len(results),
        "median_latency_seconds": round(statistics.median(durations), 2) if durations else None,
        "median_at_most_30s": statistics.median(durations) <= 30 if durations else None,
        "cost_available": len(costs) == len(completed) and bool(completed),
        "observed_cost_usd": round(sum(costs), 6) if costs else None,
        "estimated_cost_per_1000_completed_usd": (
            round(statistics.mean(costs) * 1000, 2)
            if len(costs) == len(completed) and completed else None
        ),
        "warning": "Small sample; costs from internal model usage, not external search/provider fees.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Submit five paid cases (not one)")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--out", type=Path, default=Path.home() / "Downloads" / "prawowid_v33_demo_metrics.json")
    args = parser.parse_args()
    if args.timeout < 10 or args.timeout > 600:
        parser.error("--timeout must be 10..600 seconds")
    try:
        request_json("GET", "/healthz")
    except (urllib.error.URLError, ValueError, TimeoutError):
        print("BLOCKED: local API is unavailable")
        return 2
    cases = CASES if args.all else CASES[:1]
    results = []
    for case in cases:
        result = measure(case, args.timeout)
        print(f'{result["name"]}: {result["status"]}, {result["latency_seconds"]} s, '
              f'{result["cost_usd"]} USD, {result["citations"]} sources', flush=True)
        results.append(result)
    payload = {
        "at_utc": datetime.now(UTC).isoformat(),
        "results": results,
        "summary": summarize(results),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("REPORT:", args.out)
    print("SUMMARY:", json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if all(row["status"] == "completed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
