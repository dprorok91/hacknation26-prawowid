#!/usr/bin/env bash
# Render Free demo: API and worker in one service. Not resilient production hosting.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?Configure DATABASE_URL (Neon asyncpg SSL URL) in Render}"
: "${OPENROUTER_API_KEY:?Configure OPENROUTER_API_KEY in Render}"
: "${DEMO_BASIC_USER:?Configure DEMO_BASIC_USER in Render}"
: "${DEMO_BASIC_PASSWORD:?Configure DEMO_BASIC_PASSWORD in Render}"
if (( ${#DEMO_BASIC_PASSWORD} < 16 )); then
  echo 'DEMO_BASIC_PASSWORD must have at least 16 characters' >&2
  exit 2
fi
if [[ "${PUBLIC_LLM_PROVIDER:-}" != "openrouter_all" ]]; then
  echo 'Set PUBLIC_LLM_PROVIDER=openrouter_all (no local Ollama on Render)' >&2
  exit 2
fi

.venv/bin/alembic upgrade head
# Idempotent metadata-only seeds; these never grant scraping approval.
.venv/bin/python scripts/register_source_roles.py --apply
.venv/bin/python scripts/register_official_sources.py --apply

.venv/bin/python -m prawdowid.worker &
worker_pid=$!
.venv/bin/uvicorn scripts.cloud_demo_entry:app --host 0.0.0.0 --port "${PORT:-10000}" --no-access-log &
api_pid=$!

cleanup() {
  trap - EXIT INT TERM
  kill "$worker_pid" "$api_pid" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait -n "$worker_pid" "$api_pid"
