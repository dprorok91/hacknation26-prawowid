#!/usr/bin/env bash
# Single-instance demo service ONLY. For permanent hosting use separate worker + API.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?Set external PostgreSQL connection (asyncpg URL)}"
: "${OPENROUTER_API_KEY:?Set OpenRouter API key}"
: "${DEMO_BASIC_USER:?Set demo username}"
: "${DEMO_BASIC_PASSWORD:?Set strong demo password, >=16 chars}"
if (( ${#DEMO_BASIC_PASSWORD} < 16 )); then echo 'Password too short' >&2; exit 2; fi
if [[ "${PUBLIC_LLM_PROVIDER:-}" != openrouter_all ]]; then
  echo 'Set PUBLIC_LLM_PROVIDER=openrouter_all for cloud (no local Ollama)' >&2; exit 2
fi
uv run --locked alembic upgrade head
uv run --locked python -m prawdowid.worker &
worker_pid=$!
uv run --locked uvicorn prawdowid.main:app --host 0.0.0.0 --port "${PORT:-10000}" --no-access-log &
api_pid=$!
cleanup() {
  trap - EXIT INT TERM
  kill "$api_pid" "$worker_pid" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait -n "$api_pid" "$worker_pid"
