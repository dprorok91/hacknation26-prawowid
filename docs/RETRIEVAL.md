# Retrieval Service — testable algorithm, not “vector search” handwaving

## Input
`RetrievalRequest { event: {entities[], action, place?, date_from?, date_to?}, claims[{claim_id,text,kind}], preferred_language:"pl", deadline_ms, analysis_id }`.

## Algorithm with budget/timeout
1. Normalize and canonicalize input; do not embed raw web HTML with instructions. Claim extraction must preserve quoted attribution and article publication date.
2. Compute `event_key` using normalized entity IDs + salient event action + time bucket; **candidate key for grouping only**, never proof same event. For ongoing events apply narrower timeframe.
3. Check `analysis_cache` by `request_hash + pipeline_version + corpus_snapshot`, with bounded TTL. Return only if still fresh and cited passages remain available.
4. Search existing corpus in parallel via keyword `tsvector` (index GIN, config `simple`) and pgvector cosine; exact vector scan fine for ~100-5000 passages; HNSW only after performance measurements. Use E5 prefixes `query: ...` and `passage: ...`.
5. Merge top 30 lexical and top 30 vector results using reciprocal rank fusion `RRF(d)=Σ 1/(60+rank_i(d))`; filter publication dates and source kind; rerank to 10 passages for each claim. **Event match requires entity/action/time checks**, not embedding similarity alone. Separate event-level candidate scoring from claim-level proof.
6. Coverage policy (initial tunable heuristic, not a “truth confidence”): try to retrieve at least 2 independent publishers for framing; for categorical factual claims prefer primary documents. Multiple sites copying the same press release are not independent confirmations. If corpus insufficient, initiate external discovery.
7. External discovery is fan-out (bounded `asyncio.TaskGroup` / `gather`): Google News RSS (no SLA) and GDELT metadata; optional Fact Check query for claim matches; optional Brave paid fallback. Configurable per-provider `timeout=3-5s`, semaphore/max concurrency, rate limits/circuit breaker. Stop by deadline; partial results are okay if explicitly shown.
8. Normalize URLs, remove tracking params, resolve redirects cautiously (SSRF checks per hop), canonical URL fingerprint; enqueue `FETCH_URL` with unique `(kind, resource_key, version)` in DB. Duplicate concurrent requests return same job ID.
9. Worker retrieves accessible source data, enforces license/robots/publisher rules, source timestamp, content quality, metadata. Separate trusted metadata from untrusted article text. If only title/snippet available, mark `metadata_only` and **never cite it as evidence of article-body claims**.
10. Chunk by paragraph (~350-700 tokens, overlap <=80 tokens), keep exact character offsets and immutable content version. Store `passage_id`, passage text and `embedding_model_id`. Record `published_at` and `fetched_at` separately.
11. Embed chunks in BATCHES outside transactions. Add to Postgres via idempotent upsert; keep re-embedding as separate job. Document status becomes `ready` only once passages/embeddings are transactionally associated with one content version.
12. Re-run local search after job completion (until query deadline). If unavailable or timeout, use existing corpus but explicitly report limitations. Never block indefinitely for all crawls to finish.
13. `EvidenceSet` includes passage IDs, title/publisher/date/URL, exact quote and `supports|refutes|context|irrelevant`, validation flag; source provenance is not inferred by LLM.
14. Evidence verification is per claim; unknown if no supporting reliable material. Framing comparison groups available reports by event, and quotes exact headlines/source spans; avoids attributing intention (“ukryli”, “kłamią”) without evidence.

## Relevance is not credibility
- Use semantic similarity ONLY for retrieval candidate ranking.
- Quality/independence/recency of sources judged separately and exposed transparently.
- Provider search rank, site domain, audience, or political label **cannot determine a verdict**.
- “Same event” is a multi-feature identity problem; claims from later phases must not be conflated with earlier coverage.

## Scoring policy / regression evaluation
`RetrievalMetrics`: recall@10 (annotated evidence present?), event-purity@k (correct event?), source diversity, quote-support precision, time p50/p95, external requests per analysis, and cold-cache vs warm-cache.

## What cannot be assumed
Google News RSS, GDELT and Google Fact Check **do not guarantee full article text or Polish domestic coverage**. Some domains paywall or block. Fallback is transparent insufficiency, not fabricated text. No browser search engine grants its general index for free to extensions.
