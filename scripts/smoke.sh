#!/usr/bin/env bash
set -euo pipefail
BASE="${PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}"
curl --fail --show-error "$BASE/healthz"
printf '\n'
echo 'Analysis stub intentionally returns HTTP 501 until Codex implements the pipeline.'
