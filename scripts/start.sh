#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo 'Create .env from .env.example and configure your worker before starting.' >&2
  exit 1
fi

python=$(uv run --locked python -c 'import sys; print(sys.executable)')
"$python" - <<'PY'
from prawdowid.config import get_settings

settings = get_settings()
if settings.public_llm_provider != "ollama":
    settings.worker_openrouter_key()
print(f"Public article model provider: {settings.public_llm_provider}")
PY

if curl --noproxy '*' -fsS --max-time 1 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  echo 'An API is already listening on port 8000. Stop it before starting this stack.' >&2
  exit 1
fi

docker compose --env-file .env up -d --wait --wait-timeout 60 postgres
"$python" -m alembic upgrade head

api_pid=
worker_pid=
cleanup() {
  trap - EXIT INT TERM
  for pid in "$api_pid" "$worker_pid"; do
    if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
  done
  wait || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$python" -m uvicorn prawdowid.main:app --host 127.0.0.1 --port 8000 --no-access-log &
api_pid=$!

for ((attempt = 0; attempt < 50; attempt++)); do
  if ! kill -0 "$api_pid" 2>/dev/null; then
    echo 'API exited during startup. Check the logs above.' >&2
    exit 1
  fi
  if curl --noproxy '*' -fsS --max-time 1 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    "$python" -m prawdowid.worker &
    worker_pid=$!
    echo 'Open http://127.0.0.1:8000. Press Ctrl+C to stop the API and worker.'
    wait -n "$api_pid" "$worker_pid"
    exit $?
  fi
  sleep 0.2
done
echo 'The API health check timed out on port 8000.' >&2
exit 1
