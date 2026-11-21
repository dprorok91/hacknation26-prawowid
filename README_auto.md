# PRAWDOWID — Codex MVP starter kit

**Status:** specification-first starter, NOT an implemented verification engine. Existing code runs only a health endpoint and a clearly marked `501` analysis stub. Codex should implement tasks P0-01..P0-12.

## Product
Polish-news analysis with two independent tracks: evidence-backed **claim verification** (five MSZ verdicts) and **framing comparison** (selection and emphasis of possibly true facts). Inputs: responsive WWW URL/text, Chrome/Brave MV3 right-click selection or active page, demonstration message adapter (or real Discord). No mandatory account. No gamification.

## Architectural choices
- **PostgreSQL 16 + pgvector** in Docker locally; same SQL dialect and schema in cloud. One deployable FastAPI modular monolith plus **separate worker process** from the same image. `SQLAlchemy 2`, `Alembic`, `asyncpg`, `pgvector`.
- **Postgres durable jobs** (`FOR UPDATE SKIP LOCKED` + lease + retry), idempotent discovery/fetch/upsert; no Redis needed for initial MVP.
- **Retrieval:** FTS with PostgreSQL `simple` text search + multilingual E5 embeddings + reciprocal rank fusion + event matching + claim-level evidence. External discovery adapters (GDELT, Google News RSS, Google Fact Check API, optional Brave API). A web discovery result is not evidence until sourced text is accessible and checked.
- **Local models:** Ollama `qwen3:4b` to start; `qwen3:8b` if resources permit. Sentence Transformers `intfloat/multilingual-e5-small` (384-d, CPU possible) with exact pgvector search initially. Provider interfaces also support paid hosted LLMs, but no mandatory API key in local mode.
- **Security:** untrusted-source boundary, SSRF protection, bounded inputs, no model tool execution, citation passage IDs validated server-side, audit logs without raw sensitive material.

## Start (Ubuntu 24.04)
1. Docker Engine/Compose v2, Python 3.11+, uv, and optionally Ollama installed.
2. `cp .env.example .env`
3. `./scripts/setup.sh` (installs Python environment; initializes Postgres container when Docker is available; does NOT pull multi-GB models automatically).
4. `./scripts/ollama_models.sh minimal` (opt-in: downloads local model weights).
5. `uv run uvicorn prawdowid.main:app --reload`
6. `curl http://127.0.0.1:8000/healthz` → `{"status":"ok","implementation":"starter-only"}`.
7. Open `codex/PROMPT.md` and paste it into Codex from project root. Codex implements P0 tasks and replaces the stub.

> The database and LLM are **not** required to run the starter health endpoint. To test full MVP, they must be configured and started.

## Where to read first
- `docs/ARCHITECTURE.md`: Mermaid system / retrieval / ingestion / concurrency diagrams
- `docs/RETRIEVAL.md`: deterministic retrieval plan and exact evidence handling
- `docs/CONCURRENCY.md`: 100 simultaneous requests, work deduplication, PostgreSQL jobs
- `docs/MODELS.md`: verified local model options, memory trade-offs, evaluation
- `docs/DB_SCHEMA.md`: schema, indexes, portability policy
- `docs/SECURITY.md`: prompt injection, SSRF, source handling, privacy
- `docs/MSZ_COMPLIANCE.md`: score-critical requirements from the uploaded 11-page brief
- `specs/001-core/spec.md`, `specs/001-core/plan.md`, `specs/001-core/tasks.md`: SDD artifacts
- `tasks/ISSUES.csv`: GitHub issue import/backlog
- `codex/PROMPT.md`: detailed coding-agent instructions

## Existing starter implementation
Only typed request/response contracts, ports, `/healthz`, a `501` stub for `POST /api/v1/analyses`, and basic tests. **No fake classification, no fabricated links, no fake demo completion.** This deliberate baseline lets Codex work in reviewable increments.

## Reference skills
See `docs/SKILLS.md`. Do not run an unreviewed third-party skill with unrestricted tool access or secrets.
