# PRAWDOWID — semantic claim review and free fact-check discovery

## Claim-level policy (2026 MVP)

The unit of evaluation is each **atomic statement**, not an entire article or an
arbitrarily joined set of claims. The original text and exact substring offsets
are preserved. Claims may be grouped into a broader coverage overview *after*
each has its own classification, evidence scope, retrieval and final verdict.

Two structured model passes run per extracted statement:

1. `extract` returns source-contiguous assertions, a provisional class, public /
   private / unknown evidence scope and a possible empirical test.
2. `assess` independently determines whether the *same exact statement* has a
   possible empirical verification/falsification procedure; distinguishes
   subjective evaluation, topic fragment and factual assertion; records a brief
   explanation and evidence scope. The previous label is **not** supplied.

The second response must include an empirical test if it says `verifiable`.
Otherwise classification becomes `uncertain`, rather than silently sending the
input to an external news provider. Only **publicly scoped factual claims**
enter public discovery. A conservative personal-reference filter remains as
privacy defense-in-depth, NOT as a factuality rule. Punctuation, word counts,
capitalization, and a lexicon of Polish verbs do **not** determine claim type.

If the reviewer is unavailable, the primary label and its scope remain
provisional, and the interface records a classification failure. No missing
information is turned into a positive verdict. This refactor does not claim
perfect inter-run stability of Ollama: run empirical repeated trials on your
own model/hardware to measure its real error rate and latency.

Opinion is surfaced as **Opinia**, separate from the MSZ five-category verdict
used by the existing API. A private factual assertion remains factually
investigable but cannot be confirmed using general world knowledge about the
class of objects.

## Free fact-check channels

- **Always available without an API key:** Google News RSS restricted by query
  to Polish fact-check publishers (Demagog, Konkret24, AFP Sprawdzam). This is
  *publisher/link discovery*, **not** their own official fact-check API and not
  a guarantee of accurate claim matching or coverage. It has no published
  unlimited-usage commitment and can throttle.
- **Optional:** official Google Fact Check Claim Search API. Enable the API in
  Google Cloud, obtain a restricted API key, then set `FACTCHECK_API_KEY` in
  the local `.env` (never commit the key). Consult Google Cloud's API quota
  settings for your project; we do not assert a universal unlimited free quota.
  It returns ClaimReview publisher, reviewed claim, source URL, rating and date.
  This project passes the key via `X-Goog-Api-Key` header and limits response
  size, execution time and results. No API key is transmitted in a URL.

The two searches are cached **per running worker** for 12 hours; the optional
Google API is not called if the key is absent. Repeating the same input without
restarting the worker should not repeat a successful API call. Failures are
retryable and do not replace or override the verdict. No database migration is
needed for the new fields because the report is stored as JSON.

## What prior reviews are — and are not

Prior reviews have `match_status=not_assessed` because a news query match does
not establish semantic equivalence with the input claim. A published rating is
labeled `Ocena wydawcy` in the UI. It is **not** accepted as PRAWDOWID's
`confirmed`, `false`, or `misleading` verdict. Those continue to require an
applicable, permission-eligible exact cited passage in the local evidence DB.
This also prevents topic matches involving SCT from being confused with
specific events (e.g., a proposal vs. a change already in force).

## Local verification

After installing the patch:

```bash
cd /home/lenovo/projects/hacknation26-prawowid
uv run --locked python scripts/sync_claim_contracts.py
uv run --locked ruff check .
uv run --locked pytest -q
```

A disposable migrated PostgreSQL test DB is necessary to exercise the DB tests;
check `TEST_DATABASE_URL`. Restart both API and worker, refresh the UI, and
check the visible `Szukaj też w publicznych źródłach online` option. For local
CPU models, multiple semantic calls per analysis may require increasing
`ANALYSIS_DEADLINE_SECONDS` in `.env` (e.g. 90) while keeping a bounded timeout.

Try these cases, both with and without a final period:

- `Władze Krakowa zaproponowały zmniejszenie SCT` → factual, public, search
  eligible, but not automatically confirmed.
- `SCT to fatalny pomysł` → opinion, not false and not a missing-news failure.
- `Moja krowa daje mleko` → factual, private, no public news query.
- `Strefa czystego transportu` → topic fragment, not a factual claim.
- Two different sentences → independent claim cards and source attribution.

If fact-check sites are unavailable or a key is missing, the claim report still
completes with its normal evidence limitations; provider failure does not change
its factuality assessment or verdict.
