# V33 demo — Midnight UI, online-by-default and authenticated sharing

## What changed

- The SPA keeps the existing analysis and deletion API contracts, uses a dark,
  mobile-responsive layout and presents verdicts and citations first.
- Technical details (source discovery, coverage, limitations) remain available
  inside an expandable panel; no evidence is removed from the report.
- Online search is **enabled by default in the text form**, with a visible
  checkbox to disable it for private content. The API default remains `false`
  deliberately so existing programmatic clients do not transmit private text.
- A fixed bottom activity bar shows the current queue/processing state and
  elapsed time, **not a fabricated percentage or ETA**.

## Share a temporary protected link from Ubuntu (laptop remains the server)

1. Confirm `prawdowid-api.service` and `prawdowid-worker.service` are active.
2. Install current `cloudflared` using Cloudflare's official Linux instructions:
   https://developers.cloudflare.com/tunnel/downloads/
3. From the project root run:

   ```bash
   bash scripts/share_demo_v33.sh your@email.com colleague1@email.com colleague2@email.com
   ```

4. The process prints a temporary HTTPS URL ending in `trycloudflare.com`.
   Send that URL to the **listed** people. They receive one-time login codes
   at their emails. Stop the process with Ctrl+C to revoke access.

The existing API continues to listen on `127.0.0.1:8000`. No router port
forwarding, public PostgreSQL service, Cloudflare account, or permanent domain
is required. Quick Tunnels are a short-lived hackathon demo, **not production
hosting**. Anyone granted access can spend paid LLM credits and read reports
shared through links. Do not publish the URL publicly; grant access only to
trusted emails. For a publicly anonymous service, first add hard spending
caps, abuse prevention, moderation and a production hosting strategy.

## Reproducible cost, time and E2E measurements

```bash
uv run --locked python scripts/measure_demo_v33.py
# one paid case — SCT, online search on

uv run --locked python scripts/measure_demo_v33.py --all
# five paid cases, including an official article URL

bash scripts/mvp_gate.sh
# static and database readiness (RUN_LIVE=1 for live gates)
```

The results JSON includes completed/failed cases, citations count, latency,
internal estimated model costs and a tentative cost-per-1000 projection.
Without a real end-to-end run do not claim to meet the <=30 s median gate.
Estimated model costs can exclude web search fees, networking and retries.

## Remaining MSZ competition gates — not automatically passed by this patch

- Corpus: >=100 real citable records, >=10 domains and >=2 source types.
  Run `scripts/check_corpus_ready.py`; metadata-only entries do not count.
- At least 30 human-reviewed cases across all five verdicts; macro-F1 >=.75.
- Citation coverage >=95%, citation correctness >=85%, abstention >=80%.
- Prompt-injection resilience >=90% in documented tests.
- Median <=30 seconds measured on live end-to-end analyses; full PostgreSQL
  completion and a second channel must be demonstrated, not just unit-tested.
- Project submission: <=12-slide PDF, <=3-minute demonstration video, source
  permissions, architectural and threat model docs, reproducible evaluation,
  and cost estimate for 1000 queries.

See `scripts/mvp_gate.sh` and `docs/MSZ_COMPLIANCE.md` for existing mechanisms.
This patch moves UI/deployment/measurement forward; it does **not** claim the
remaining quality or corpus thresholds have been reached.

## Off-laptop deployment and shared password

For an optional Render Free + Neon Free blueprint and Basic login, see `docs/HOSTED_DEMO_V33.md`. This is a hackathon demo, not a hardened production deployment.
