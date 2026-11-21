# Concurrent requests: two independent problems

## A. Parallel requests to backend (N users)
- API is async/stateless; Postgres pool sized (e.g. 5-10 per process; one initial API worker), queries bounded; no long transaction holds an LLM or HTTP request.
- Same normalized analysis: `analyses.input_hash, pipeline_version, source_snapshot` unique if eligible for caching. POST returns `202 {analysis_id,status:"processing"}` and GET `/api/v1/analyses/{id}` poll (or 200 cached). No duplicate LLM calls while run active. Request-specific input may be redacted; do not reuse results across privacy-sensitive text without explicit scope check.
- Concurrency budgets: provider semaphore, LLM concurrency (`1` local on small GPU), max outstanding fetch jobs, request timeout. Backpressure returns `429`/`503` technical status (not a factual verdict).

## B. Two workers fetching/updating same URL
- `documents.canonical_url UNIQUE`; `jobs UNIQUE(kind, dedupe_key, generation)`; API only enqueues an idempotent job.
- Worker job acquisition is a **short transaction**: `SELECT id FROM jobs WHERE state='queued' OR (state='leased' AND lease_until < now()) ORDER BY priority,created_at FOR UPDATE SKIP LOCKED LIMIT 1`, update state/owner/lease_until; COMMIT immediately. This is PostgreSQL-specific intentional behavior.
- Then perform network fetch/embedding OUTSIDE SQL transaction. Heartbeat only for tasks longer than lease; expiry lets another worker retry. Completion uses `UPDATE ... WHERE id=:id AND owner=:owner AND state='leased'`, preventing an expired worker from claiming success.
- `INSERT INTO documents (...) ... ON CONFLICT (canonical_url) DO UPDATE` with version/content hash comparison; protect against old crawls overwriting newer versions. Insert passages versioned and `ON CONFLICT (document_id,version,ordinal) DO UPDATE` (or immutably insert); mark ready in one transaction.
- `READ COMMITTED`, short transactions, unique constraints, retries with exponential backoff/jitter. “Exactly once” is not promised: **at-least-once jobs + idempotent effects**.
- Multiple API / workers can run in cloud against same Postgres. No process-local mutex as correctness mechanism; `asyncio.Lock` may coalesce within a worker but cannot coordinate replicas.

## C. Provider-side concurrency
- Per-provider rate limit and total concurrency cap; no unbounded `gather` of 100 URLs. GDELT may throttle despite free access; treat HTTP 429 and non-JSON notices distinctly.
- `HTTP timeout` 3–5s discovery; max 5 candidates fetched for standard analysis; 1–2 attempts, retry only transient failures.

## D. Test plan (mandatory)
1. 40 simultaneous identical URL POSTs -> one logical analysis/limited duplicate work, same analysis ID where cache scope allows.
2. 20 simultaneous requests for overlapping story -> unique canonical URL documents and passages; no duplicate fetch until TTL.
3. Two workers lease jobs -> no same live job owned twice.
4. Kill worker after lease -> job re-leased after expiry; no permanent `processing` state.
5. Simulate timeouts, 429, malformed RSS, failed model -> explicit system status, never `insufficient_evidence` for an API failure alone.
6. Run integration suite with **real PostgreSQL**, not SQLite mocks.

## Why NOT SQLite as primary database?
SQLite WAL allows concurrent readers and a single writer, but serialized writer contention and lack of native `SKIP LOCKED` require an alternate work-queue implementation. PostgreSQL locally is free and removes a near-term rewrite. SQLAlchemy interfaces still isolate persistence if future storage changes.
