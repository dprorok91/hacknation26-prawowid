#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PUBLIC_API_BASE_URL="${PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}"
curl --fail --show-error --max-time 5 "$PUBLIC_API_BASE_URL/healthz"
printf '\n'
if [[ -n "${SMOKE_URL:-}" ]]; then
  uv run --locked python - <<'PY'
import os
from runpy import run_path

import httpx

poll = run_path("scripts/evaluate.py")["await_result"]
with httpx.Client(base_url=os.environ["PUBLIC_API_BASE_URL"], timeout=8, trust_env=False) as client:
    record, _, _ = poll(
        client, "/api/v1/analyses",
        {"kind": "url", "content": os.environ["SMOKE_URL"]},
        float(os.getenv("SMOKE_TIMEOUT", "40")),
    )
    report = record.get("report") or {}
    sources = report.get("sources", [])
    print(f"Analysis status={record['status']}, citations={len(sources)}.")
    raise SystemExit(0 if record["status"] == "completed" and sources else 1)
PY
fi
