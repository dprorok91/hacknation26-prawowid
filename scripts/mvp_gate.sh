#!/usr/bin/env bash
# Non-destructive MVP verification. Does not pull models, alter migrations or
# start/stop Postgres. Optionally checks the already-running API and worker.
set -uo pipefail
cd "$(dirname "$0")/.."

RESULTS_DIR="${MVP_RESULTS_DIR:-$HOME/Downloads/prawdowid-mvp-check}"
mkdir -p "$RESULTS_DIR"
SUMMARY="$RESULTS_DIR/SUMMARY.txt"
: > "$SUMMARY"
failed=0
blocked=0

note() {
  printf '%s\n' "$*" | tee -a "$SUMMARY"
}

run_check() {
  local name="$1"; shift
  note "RUN $name"
  if "$@" > "$RESULTS_DIR/$name.log" 2>&1; then
    note "PASS $name"
  else
    note "FAIL $name (see $name.log)"
    failed=$((failed+1))
  fi
}

if ! command -v uv >/dev/null 2>&1; then
  note "BLOCKED: uv is not installed. See README installation instructions."
  exit 2
fi

note "PRAWDOWID MVP CHECK"
note "UTC: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
note "Git revision: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

run_check lint uv run --locked ruff check .
run_check unit_tests uv run --locked pytest -q -ra
run_check corpus_ready uv run --locked python scripts/check_corpus_ready.py

if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  note "BLOCKED integration: set TEST_DATABASE_URL to a migrated disposable PostgreSQL test DB."
  blocked=$((blocked+1))
else
  # The full test suite was already run with TEST_DATABASE_URL above.
  # Report the DB test status separately for review of its -ra summary.
  note "INFO integration: TEST_DATABASE_URL was supplied; inspect unit_tests.log for skips."
fi

if [[ "${RUN_LIVE:-0}" == "1" ]]; then
  API="${PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}"
  run_check live_health curl --fail --silent --show-error --max-time 5 "$API/healthz"
  if [[ -n "${SMOKE_URL:-}" ]]; then
    run_check live_url_smoke bash scripts/smoke.sh
  else
    note "BLOCKED live URL smoke: set SMOKE_URL to an approved public article URL."
    blocked=$((blocked+1))
  fi
  if [[ "${RUN_EVAL:-0}" == "1" ]]; then
    note "INFO evaluation: requires a running API + worker; may take several minutes."
    run_check reviewed_evaluation uv run --locked python scripts/evaluate.py \
      tests/fixtures/reviewed_cases.jsonl --base-url "$API" --both-channels \
      --gate-set reviewed \
      --out "$RESULTS_DIR/reviewed-results.json"
    run_check injection_evaluation uv run --locked python scripts/evaluate.py \
      tests/fixtures/injection_cases.jsonl --base-url "$API" \
      --gate-set injection \
      --out "$RESULTS_DIR/injection-results.json"
  else
    note "BLOCKED performance/accuracy: set RUN_EVAL=1 for live evaluation."
    blocked=$((blocked+1))
  fi
else
  note "BLOCKED live smoke/evaluation: set RUN_LIVE=1 with API + worker running."
  blocked=$((blocked+1))
fi

note "SUMMARY: failed=$failed blocked=$blocked"
note "Logs saved to $RESULTS_DIR"
if (( failed > 0 )); then exit 1; fi
if (( blocked > 0 )); then exit 2; fi
exit 0
