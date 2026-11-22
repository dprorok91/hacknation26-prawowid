# Free news discovery for development

Primary providers: Google News RSS and GDELT DOC, no API key required; both may
throttle or change without notice. `NewsData.io` is an **optional fallback**,
not used when two distinct publishers have already been discovered.
NewsData free plan: 200 credits/day and up to 10 articles/request, delayed
approximately 12 hours; latest endpoint covers ~48 hours, not historical articles.

## Setup

1. `uv run --locked alembic upgrade head` (adds only discovery cache and credit ledger).
2. `DISCOVERY_CACHE_HOURS=12` optional (1–24 hours). Cache stores only public
   article URL/title/publisher/date, keyed by a SHA-256 event fingerprint, for
   both empty and nonempty successful searches. Provider errors are not cached.
3. Optional NewsData fallback: register at https://newsdata.io, then locally set
   `ENABLE_NEWSDATA_FALLBACK=true`, `NEWSDATA_API_KEY=...`,
   `NEWSDATA_DAILY_CREDIT_BUDGET=20`. Secrets stay in `.env` (never commit it).
   Credits are atomically reserved in PostgreSQL **before** a request, so failed
   requests conservatively count against the configured local budget.
   Provider quotas still apply independently. Only one page is requested.
4. Start API and worker. For **text**, explicitly check the web-search option.
   URLs already use public discovery. No submitted private text is persisted
   in the discovery cache; search anchors go to external providers only on opt-in.

## Evidence and storage

- Discovered links are stored in `documents` as `metadata_only`, with no passages.
- Only domains explicitly approved for text reuse may be queued for article fetch.
- SafeWeb enforces URL, redirect, DNS, robots and fetch restrictions.
- Metadata, snippets and provider judgments never become citations or verdicts.
- Source approvals are not automatic. Review publisher policies manually.
- For reproducible tests, recorded MockTransport responses (offline) should be
  preferred to live provider requests. Provider rate caps still apply to real runs.

## Verify

```bash
uv run --locked alembic upgrade head
uv run --locked pytest -q
uv run --locked ruff check .
# with API/worker running, and public-search checkbox checked, submit a claim
# count discovered metadata versus evidence-ready documents
uv run --locked python scripts/check_corpus_ready.py
uv run --locked python scripts/news_status.py
```

Current code does not magically turn source search into evidence-ready documents.
Ingestion of approved source text requires an actual fetch worker and suitable
article text. NewsData will NOT solve historical queries outside its latest window.
