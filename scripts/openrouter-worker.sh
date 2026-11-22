#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export PUBLIC_LLM_PROVIDER=openrouter_all
if ! uv run --locked python -c 'from prawdowid.config import Settings; Settings().worker_openrouter_key()' 2>/dev/null; then
  IFS= read -r -s -p 'OpenRouter API key (input is hidden): ' OPENROUTER_API_KEY
  echo
  export OPENROUTER_API_KEY
fi
echo 'Starting worker with OpenRouter for all analyses, including pasted text and message-demo.'
exec uv run --locked python -m prawdowid.worker
