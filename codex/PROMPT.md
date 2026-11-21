# Paste into Codex CLI (run from repository root)

You are lead software engineer and security reviewer implementing the PRAWDOWID hackathon MVP. Work in the current repository and **implement code, not just a plan**. The existing repo is a specification-first starter. No verifiable analysis exists yet; `/api/v1/analyses` returns 501 deliberately. Do not claim finished until tests and real end-to-end work are demonstrated.

READ FIRST: `AGENTS.md`, `.specify/memory/constitution.md`, `specs/001-core/spec.md`, `specs/001-core/plan.md`, `tasks/TASKS.md`, `docs/RETRIEVAL.md`, `docs/CONCURRENCY.md`, `docs/DB_SCHEMA.md`, `docs/SECURITY.md`, `docs/MSZ_COMPLIANCE.md`, `docs/MODELS.md`, `docs/SKILLS.md`, `docs/ARCHITECTURE.md` and original MSZ PDF if made available.

READ EXTERNAL SKILLS: GitHub `github/spec-kit`; `wshobson/agents` skills `backend-development/api-design-principles`, `backend-development/architecture-patterns`, `llm-application-dev/rag-implementation`, `llm-application-dev/llm-evaluation`, `security-scanning/stride-analysis-patterns`, `python-development/python-testing-patterns`; and `GoogleChrome/modern-web-guidance/skills/chrome-extensions/SKILL.md` with context-menus, permissions and side-panel references. Use if installed, otherwise read the public source. Do not silently invent skill instructions.

RUN (without asking for each step) the sequence P0-00 through P0-11 in `tasks/TASKS.md` as time and context permit. Keep small commits or clear checkpoint summaries, report each task as PASS/PARTIAL/BLOCKED with actual commands and outputs. If time is limited, prioritize true vertical E2E (`URL/text → claim extraction → safe retrieval → source evidence → verdict/abstention → JSON → logging → second channel`) over optional chrome.

**NON-NEGOTIABLE architecture:**
- PostgreSQL16 + pgvector as DEFAULT local+cloud DB (Docker compose). SQLAlchemy2 async, Alembic migrations, Pydantic2 types, clear ports/adapters; do not build multiple microservices.
- Two processes same codebase: FastAPI and durable DB worker. Jobs atomically leased via `FOR UPDATE SKIP LOCKED`; unique dedup keys; timeout/retry/lease; HTTP and model inference outside transactions. `ON CONFLICT` idempotent upserts; tests for 40 concurrent requests and two workers.
- `RetrievalService` is not one embedding call: event/date/entity extraction → local FTS and vector top-k → RRF → coverage check → bounded GDELT/GoogleNews RSS/FactCheck discovery → unique fetch jobs → safe fetch/passages/embeddings → claim-specific retrieval → passage-backed evidence. Distinguish matching the same event from matching the same topic.
- Use Sentence Transformers multilingual-e5-small, 384-D with mandatory `query:`/`passage:` prefixes, model version metadata. LLM Ollama `qwen3:4b` JSON schema constrained + validation, another provider adapter optional; local small model may be too slow or inaccurate for a 30s p50. Do not hide the shortfall.
- No invented evidence or citations. A citation must reference exact passage ID with checked quote. Claims have one of 5 MSZ verdicts when evaluated; technical/API/input failures use separate statuses. Framing is NOT an ideology/truth score and can show two true facts supporting different editorials.
- SSRF proofing, least privilege extension, untrusted news as data (no tool execution); auditing, cost/latency, opt-in data retention. No unauthorized full-text archival, no bypass of paywall.
- Minimal UI; web and a second automated messaging demo adapter use same orchestrator. Add Chrome MV3 context menu/side panel only after P0 E2E if time.
- Tests: pytest offline mocked APIs and real-Postgres integration; 30+ reference cases where possible. Never hardcode expected reference answers in prompts or production logic. No claims of MSZ thresholds until measured.

IMPLEMENTATION DISCIPLINE:
1. Examine files before editing and list blockers briefly.
2. Start with migrations/ports and build incremental vertical slices; each task includes tests.
3. Run `uv sync --extra dev`, `pytest`, `ruff`, database migration, end-to-end smoke where dependencies present.
4. Update docs, exact model/provider/source costs and restrictions; never fabricate an API quota, citation or demo result.
5. Keep `.env` and any downloaded models out of Git. Avoid `eval`, direct arbitrary URL fetch without guard, and unlimited asynchronous background tasks.
6. Deliver a clear final summary: implemented / not implemented, tests performed and actual results, run commands, remaining risks.

Start now with P0-00 and P0-01, then continue through as many tasks as can be correctly implemented within the current session. Do not stop after generating a design or ask me to repeat requirements.
