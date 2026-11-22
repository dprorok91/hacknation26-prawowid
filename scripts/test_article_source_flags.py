"""End-to-end regression for an article containing several factual claims.

Uses the existing local approved corpus by default. Does not invent sources;
missing source categories are explicit. Opt into external search with --online.

  uv run --locked python scripts/test_article_source_flags.py
  uv run --locked python scripts/test_article_source_flags.py --online
  uv run --locked python scripts/test_article_source_flags.py --text-file article.txt
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXAMPLE_ARTICLE = (
    "Strefa Czystego Transportu w Krakowie zaczęła obowiązywać 1 stycznia 2026 roku. "
    "Od 1 stycznia 2026 roku w Krakowie wszystkie samochody spalinowe mają "
    "bezwzględny zakaz wjazdu do SCT, bez żadnych wyjątków. "
    "Pojazdy niespełniające wymogów SCT mogą wjeżdżać do strefy do końca 2028 roku "
    "po uiszczeniu opłaty. "
    "Każdy pojazd elektryczny jest zakazany w strefie SCT w Krakowie."
)
CATEGORIES = ("official", "major_media", "fact_check", "other_media")


def api(base: str, method: str, path: str, payload: dict | None = None) -> dict:
    request = Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def summarize(report: dict) -> dict:
    """Explicitly distinguish observed citations from missing classes."""
    all_sources = {row["source_id"]: row for row in report.get("sources", [])}
    found = set()
    per_claim = []
    for claim in report.get("claims", []):
        citations = []
        for source_id in claim.get("evidence_source_ids", []):
            source = all_sources.get(source_id)
            if not source:
                continue
            label = source.get("source_category", "other_media")
            found.add(label)
            citations.append({
                "category": label,
                "role": source.get("source_role"),
                "publisher": source.get("publisher"),
                "url": source.get("url"),
                "quote": source.get("exact_quote"),
            })
        per_claim.append({
            "claim": claim.get("text"), "verdict": claim.get("verdict"),
            "citations": citations,
        })
    return {
        "claims": per_claim,
        "by_category": dict(Counter(source.get("source_category") for source in all_sources.values())),
        "categories_with_cited_evidence": sorted(found),
        "categories_without_cited_evidence": [cat for cat in CATEGORIES if cat not in found],
        "limitations": report.get("limitations", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--online", action="store_true", help="May incur paid model/search costs")
    parser.add_argument("--text-file", type=Path, help="UTF-8 article to test (up to 20k chars)")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    text = args.text_file.read_text(encoding="utf-8") if args.text_file else EXAMPLE_ARTICLE
    if not text.strip():
        parser.error("Article text must not be empty")
    if len(text) > 20000:
        parser.error("Article exceeds 20,000 characters; provide a shorter excerpt")
    try:
        result = api(args.base, "POST", "/api/v1/analyses", {
            "kind": "text", "content": text, "language": "pl", "web_search": args.online,
        })
        analysis_id = result["analysis_id"]
        print("ANALYSIS ID:", analysis_id)
        deadline = time.monotonic() + args.timeout
        while result.get("status") in ("queued", "processing", None):
            if time.monotonic() >= deadline:
                raise TimeoutError("Analysis still pending; ID printed above for retry")
            time.sleep(2)
            result = api(args.base, "GET", f"/api/v1/analyses/{analysis_id}")
        print("STATUS:", result.get("status"), "ERROR:", result.get("error_code"))
        if result.get("status") == "completed":
            print(json.dumps(summarize(result.get("report") or {}), indent=2, ensure_ascii=False))
        else:
            print("No report; check API and worker logs.")
    except (HTTPError, URLError, TimeoutError) as exc:
        parser.exit(1, f"Analysis failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
