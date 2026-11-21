# Implementation tasks

Single source: `tasks/TASKS.md` and `tasks/ISSUES.csv` (includes scope, dependency, acceptance tests).

Checkpoint sequence: T00 → T01 → T02 → T03 → T04 → T05 → T06 → T07 → T08 → T09 → T10 → T11; T12 optional. Do not start UI until a deterministic evidence-backed E2E pipeline passes.

## Implementation checkpoints

- [x] P0-00 — Locked setup, health and offline tests verified locally. Added a pinned CI workflow and tested settings validation.
- [x] P0-01 — Typed HTTP input, errors, polling and source contracts. Both submission routes call the same service.
- [x] P0-02 — Verified Alembic upgrade, downgrade and re-upgrade against PostgreSQL 16 and checked pgvector and uniqueness constraints.
- [x] P0-03 — Verified 40 parallel requests, two overlapping workers, retry exhaustion and stale lease fencing against PostgreSQL. The worker tests use fixture handlers.
- [x] P0-04 — Verified the bounded discovery adapters with nine offline transport tests.
- [x] P0-05 — Verified safe fetch, versioning and metadata-only storage. The PostgreSQL corpus test preserves previous passages and rejects stale updates.
- [ ] P0-06 — Hybrid retrieval
- [ ] P0-07 — Evidence-bound verification
- [ ] P0-08 — Narrative comparison
- [ ] P0-09 — Report persistence and web UI
- [ ] P0-10 — Second channel, privacy and observability
- [ ] P0-11 — MSZ evaluation and package
- [ ] P1-12 — Chrome/Brave extension after P0 verification
