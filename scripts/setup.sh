#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv: https://docs.astral.sh/uv/'; exit 1; }
[[ -f .env ]] || cp .env.example .env
uv sync --extra dev
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  docker compose --env-file .env up -d postgres
  echo 'Postgres starting. Migrations are a Codex implementation task (P0-02).'
else
  echo 'Docker not found; install Docker Engine + compose plugin for DB integration.'
fi
echo 'Starter ready. Run: uv run uvicorn prawdowid.main:app --reload'
