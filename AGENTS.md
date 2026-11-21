# Codex working agreement

The source of truth is `.specify/memory/constitution.md`, `specs/001-core/`, `docs/`, `tasks/TASKS.md`. Read `codex/PROMPT.md` for the planned implementation order. Work test-first for retrieval, persistence concurrency, and safety. Keep architecture as single FastAPI backend + Postgres worker process. No auto-generated unsupported factual claims, real source citations only.

IMPORTANT: Run tests after each task. Do not weaken tests to make them pass. If no sources are available return a transparent, typed insufficiency or infrastructure error. Never claim screenshots/images supported until implemented.
