# Source-first search V2 — Google News + Parallel

Opt-in for public searches remains the existing `web_search` switch. Claim-level retrieval
never sends private or first-person assertions to OpenRouter.

## Routing

1. For every eligible public claim, two deterministic Polish queries are sent to
   Google News RSS (subject to existing provider limits/cache). The query planner
   uses different strategies for dates, universal exceptions, and causal effects.
2. If no **direct official URL** was returned, query Parallel Turbo through
   OpenRouter (`max_tool_calls=1`, `max_uses=1`, up to 5 citations).
3. Parallel Basic is tried only if Turbo fails to return usable cited URLs.
   HTTP 401/402/403 or 429 does not trigger an extra paid search.
4. Deduplicate by canonical URL, preserve individual provider batches and query
   traces under `report.search.queries`.
5. Every result is **metadata only**. The existing licensed-corpus approval,
   ingestion and evidence gates remain unchanged. Model citations are NOT quotes.

## Configuration

The worker reads `.env` using a separate Pydantic Settings class:

```dotenv
OPENROUTER_API_KEY=sk-or-...
PARALLEL_SEARCH_ENABLED=true
PARALLEL_DAILY_CALLS=30
PARALLEL_TIMEOUT_SECONDS=8
ENABLE_GDELT_SEARCH=false
```

`PARALLEL_DAILY_CALLS=0` disables paid search. `PARALLEL_SEARCH_MODEL` defaults to `openai/gpt-4.1-mini`. Each attempted paid call reserves one
credit atomically (including failures) via the existing PostgreSQL daily budget.
The configured maximum is **200 calls per UTC day**, enforced across workers by PostgreSQL. Two requests for one claim happen only
when Turbo returns no usable citations. Queries and provider statuses are
recorded; secrets and raw OpenRouter model output are not persisted.

This is a **call-count cap**, not a guaranteed USD spending limit. Server-side
search fees and token costs depend on OpenRouter's current prices and API behaviour.

## Limitations

- Google News RSS may return aggregator URLs. They are *not* automatically
  rewritten to publisher URLs; unverified domain resolution is unsafe.
- A publication link is not a verified evidence passage. Existing licensing
  approvals are still needed for article text.
- Parallel's server-tool result is cited links from a model, **not full SERP**.
- Neither benchmark counts nor this implementation prove source relevance.
- GDELT is disabled by default due observed HTTP 429, can be reenabled explicitly.
- The search is skipped for private claims and ordinary text without web opt-in.
- Provider failures do not erase successfully retrieved Google News candidates.
- No live API calls were made to verify pricing or provider output in this patch.

## Verifying deployment

Run `uv run --locked ruff check .`, `uv run --locked pytest -q` and analyse
three earlier SCT claims with online search enabled. Inspect `report.search.queries`,
`report.search.provider_errors`, `report.discovered_links`, and `report.sources`.
Expect `report.sources` to remain empty until an approved document can supply an
exact quote; never treat a retrieved link as a confirmed fact.

## Snapshot compatibility

This patch targets the repository snapshot `prawowid_current_repo_20261122_011114.zip`.
It extends `DiscoveryStatus.queries` but does not change current `DiscoveryReport`
or fabricate evidence from link metadata. Tests using `TEST_DATABASE_URL` are still
skipped in environments without a disposable migrated PostgreSQL database.

The report retains up to 200 deduplicated link-metadata rows across at most three
claims; PostgreSQL caches up to 30 per provider query. Model citations are never
treated as verified evidence. If OpenRouter does not report an actual web-tool
invocation, its annotations are rejected rather than trusted.
