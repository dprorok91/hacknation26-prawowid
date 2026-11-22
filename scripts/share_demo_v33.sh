#!/usr/bin/env bash
# Share the already-running, LOCAL PRAWDOWID server through an authenticated
# temporary Cloudflare Tunnel. Never opens firewall or Postgres ports.
set -euo pipefail

usage() {
  cat <<'TXT'
Usage: bash scripts/share_demo_v33.sh alice@example.com [bob@example.com ...]

Every listed visitor receives a one-time email PIN before the demo opens.
The generated trycloudflare.com URL is temporary and disappears when stopped.
Run this only with trusted visitors: verified access can consume paid model tokens.
TXT
}

if (( $# < 1 )); then usage; exit 2; fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo 'cloudflared missing. Install from https://developers.cloudflare.com/tunnel/downloads/' >&2
  exit 2
fi
for address in "$@"; do
  if [[ ! "$address" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
    echo "Invalid email address: $address" >&2
    exit 2
  fi
done
if ! curl -fsS --max-time 5 http://127.0.0.1:8000/healthz >/dev/null; then
  echo 'Local FastAPI is not healthy on 127.0.0.1:8000' >&2
  exit 1
fi
if command -v systemctl >/dev/null 2>&1 && ! systemctl --user is-active --quiet prawdowid-worker.service; then
  echo 'Background worker is not active; resolve before sharing a demo.' >&2
  exit 1
fi

flags=()
for address in "$@"; do flags+=(--allowed-mail "$address"); done
cat <<'TXT'

PRAWDOWID authenticated demo tunnel
===================================
- FastAPI / Worker: existing local user services (no new server started)
- Security: email one-time PIN required; only supplied addresses may connect
- Data & cost: approved visitors can submit analyses and consume OpenRouter credits
- Stop: Ctrl+C; the temporary URL will no longer work

Look for the https://*.trycloudflare.com URL below, then send it to visitors.
TXT
exec cloudflared tunnel --url http://127.0.0.1:8000 "${flags[@]}"
