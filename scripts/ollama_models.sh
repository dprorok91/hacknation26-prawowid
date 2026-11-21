#!/usr/bin/env bash
set -euo pipefail
if ! command -v ollama >/dev/null; then echo 'Install Ollama: https://ollama.com/download'; exit 1; fi
case "${1:-minimal}" in
  minimal) ollama pull qwen3:4b ;;
  enhanced) ollama pull qwen3:8b ;;
  gemma) ollama pull gemma3:4b ;;
  embedding) ollama pull embeddinggemma ;;
  *) echo 'Usage: bash scripts/ollama_models.sh {minimal|enhanced|gemma|embedding}'; exit 2;;
esac
