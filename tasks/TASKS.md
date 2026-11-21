# Backlog for Codex — implement in order

Each task is a reviewable PR or commit. Replace scaffold stubs only when tests demonstrate actual behavior. Where external data unavailable, return honest errors. DO NOT create fabricated citations or hardcoded verdicts.

## P0-00: setup and reproducible build
**Deliver:** uv lock, Docker compose Postgres pgvector, Alembic, env validation, `make test`, CI offline checks, deterministic tests. **AC:** fresh clone + scripts setup + health + tests; no secret requirement for offline tests.

## P0-01: API contract, input gate, error taxonomy
**Deliver:** `POST /api/v1/analyses`, `GET /api/v1/analyses/{id}`, `POST /api/v1/adapters/message-demo`, source retrieval, Pydantic strict request/response. **AC:** URL/text, `ChatGPT Sol`, image/no URL, excessive input, bad scheme, invalid URL; explicit status vs five verdicts; identical core pipeline for both channels.

## P0-02: SQL models/migrations and ports
**Deliver:** Alembic tables & indexes from DB_SCHEMA; Pgvector dimension 384; repository protocols and SQLAlchemy adapters; TTL/version. **AC:** migration upgrade/downgrade on fresh PG; unique URL and job constraints; no domain code imports SQLAlchemy; production runtime never silently `create_all`.

## P0-03: durable queue and concurrent ingestion coordination
**Deliver:** worker CLI, lease/heartbeat/retry/dead-letter, duplicate task coalescing with unique keys, `SKIP LOCKED`, bounded task batches. **AC:** concurrent 40 requests / 2 workers -> unique docs, no double-owned lease, restart/lease expiration reliable, no DB transaction across HTTP/LLM.

## P0-04: source discovery adapters
**Deliver:** `GoogleNewsRssDiscovery`, `GdeltDiscovery`, optional `FactCheckDiscovery`, optional `BraveDiscovery`; normalized `DiscoveredArticle` with provider/title/url/date/domain. **AC:** malformed provider response, 429, timeout, no results, breaker/rate cap; mock HTTP offline; provider metadata never accepted as evidence.

## P0-05: safe fetch + document quality + versioning
**Deliver:** controlled fetch, SSRF/DNS/redirect checks, size/content-type/time limits, HTML extraction only where permitted, published and fetched dates, license/storage flags, canonical URL, changed-content revision. **AC:** block localhost/169.254.169.254/private IP/redirect; repeated same URL no duplicate row; changed content different version; metadata-only never cited as evidence.

## P0-06: indexing and hybrid retrieval (HARD)
**Deliver:** paragraph chunks/offsets, embeddings with E5 `query:`/`passage:`, PgVector exact cosine, GIN simple FTS, RRF top-k, event match entities/action/date, per-claim retrieve and provenance. **AC:** 10+ Polish fixture cases, expected passage in top10; distinguish two separate events with same topic; don't mix embedding model versions; empty corpus returns no evidence.

## P0-07: evidence-bound verification
**Deliver:** LLM adapter for Ollama `qwen3:4b` structured output, extraction to atomic propositions, evidence support/refutation with verified passage IDs, five verdicts, abstention, rule-based no-claim classification. **AC:** citation quote literal substring of retrieved passage; contradictory sources and missing evidence abstain; no claims scored on publication domain alone; no LLM “confidence” fabricated.

## P0-08: narrative framing comparison (DIFFERENTIATOR)
**Deliver:** `EventFrame` with article link, quoted headline, supporting spans, key emphasis themes, selected facts; common ground, differing emphasis, chronological context. **AC:** minimum 2 independently matched sources; inability to match -> empty framing with reason; preserve possibility of two true consequences; no mind-reading motives or ideological blanket label.

## P0-09: report persistence and thin web UI
**Deliver:** `/analyses` JSON and responsive simple web UI; per-claim links; framing panels; source timestamps; status/polling; shareable public report for permitted public URLs only. **AC:** no sign-in, accessible on mobile, sub-30s p50 target measured not asserted; source links navigable.

## P0-10: second channel/adapter, privacy and observability
**Deliver:** fully automatic message-demo endpoint or real Discord bot; shared orchestrator; audit latency/token/cost/error, redaction, deletion and retention config. **AC:** same source snapshot gives same core result via both endpoints; no secrets/raw PII in logs; errors distinguish source outage from evidence insufficiency.

## P0-11: reproducible MSZ tests and package
**Deliver:** 30+ labeled public dev cases (with rights), 10+ prompt injection fixtures, test runner macro-F1/citation/abstention/latency/cost; 100 REAL documents 10 domains, 2 types. README, licencing list, 1000-query estimate, <=12 slides PDF, <=3 min demo, risks. **AC:** all measured and documented; report failures; no hardcoded labels in prompts; no fake corpus counts.

## P1-12: Chrome/Brave extension (defer if P0 not green)
**Deliver:** MV3 service worker, `contextMenus` selection and page action, side panel, user-visible feedback; POST to same API; minimum permission scope; no content auto-upload. **AC:** select+right-click sends selection; click icon sends URL; no blank panel; no silent access; 429/unsupported/error UI; works unpacked Chromium.

## Evaluation gates (must print actual results)
- macro-F1>=.75, citation coverage>=.95, correct evidence quotes>=.85, abstention>=.80, injection resistance>=.90, p50<=30s. If not achieved, document gaps, don't claim achieved.
