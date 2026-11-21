# PostgreSQL schema + indexing contract

Implementation must use SQLAlchemy 2 models + Alembic migrations; **no `create_all()` as a substitute for production migrations**. Docker image `pgvector/pgvector:pg16` provides PostgreSQL 16 with extension. Run `CREATE EXTENSION IF NOT EXISTS vector` in first migration (with proper role privileges).

## Tables (minimum)
- `sources(id, domain UNIQUE, display_name, source_type ENUM[news,primary,factcheck], policy_url, license_label, enabled, approved_at)`.
- `documents(id, source_id, canonical_url UNIQUE, url_hash, title, published_at nullable, fetched_at, content_hash, content_version, text_license, retrieval_status, original_language, indexed_at)`. Preserve **older** content version if a later update modifies the text.
- `passages(id, document_id, content_version, ordinal, char_start, char_end, text, source_span_hash, embedding vector(384), embedding_model_id)` with `UNIQUE(document_id,content_version,ordinal)`. Only store passages where terms allow; metadata-only documents have none.
- `events(id, event_key, label, start_at, end_at, language, created_at)` and `event_documents(event_id,document_id,match_score,event_match_reason) UNIQUE(event_id,document_id)`.
- `analyses(id, input_hash, status ENUM[queued,processing,completed,failed,needs_clarification], input_type, input_redacted, pipeline_version, corpus_snapshot, created_at, updated_at, finished_at, error_code, response_json)`; dedupe only non-sensitive repeated public URL analyses, not arbitrary private selected text.
- `claims(id, analysis_id, text, kind ENUM[verifiable,opinion,non_claim,unsupported_scope], verdict nullable, rationale, created_at)`; five verdicts when evaluated: `confirmed,false,misleading,insufficient_evidence,not_verifiable`.
- `evidence(id, claim_id, passage_id, relation ENUM[supports,refutes,context], exact_quote, validation_passed, checked_at)`; evidence MUST be traceable to source passage and exact literal quote.
- `narrative_frames(id, analysis_id, document_id, emphasis_json, omission_comparison_json, highlighted_passage_ids, uncertainty_text)`; **no intent or political-score column**.
- `jobs(id, kind, dedupe_key, generation, state ENUM[queued,leased,completed,dead], priority, attempts, lease_owner, lease_until, run_after, payload_json, error_code, created_at, updated_at) UNIQUE(kind,dedupe_key,generation)`.
- `audit_events(id, analysis_id nullable, event_type, at, source_ids_json, model_name, latency_ms, tokens_in, tokens_out, estimated_cost_usd, redacted_metadata_json)`.

## Indices
- BTree: `documents(canonical_url) UNIQUE`, `documents(source_id,published_at DESC)`, `passages(document_id,content_version,ordinal) UNIQUE`, `jobs(state,run_after,priority)`, `analyses(input_hash,pipeline_version)`.
- Full-text `GIN`: `to_tsvector('simple', coalesce(passages.text,''))` + separate title search. Prefer `simple` initially because Polish morphology needs special dictionaries/morphological preprocessing; document and tune.
- Vector: `passages.embedding vector(384)` with `embedding_model_id`. For small corpus, exact `ORDER BY embedding <=> :query LIMIT 30` (cosine) is sufficient; enable HNSW index `(embedding vector_cosine_ops)` only when measured. No forced distributed vector DB.
- Freshness/version: model versions stored per passage, never compare vectors from different models; reindex on model upgrade. Index rebuilding is expected during migrations, **domain and API remain stable**.

## Query design
Two distinct queries: **event retrieval** (candidates from headlines, entities, dates) and **claim retrieval** (evidence passages). Ranking: FTS and vector each top 30 then application RRF. Apply at most one small rerank step using low-cost rules; avoid feeding 100 articles to LLM.

## Migration to cloud
1. Start with local Docker PostgreSQL and Alembic.
2. Use managed PostgreSQL supporting pgvector, migrate DB with pg_dump/restore, change `DATABASE_URL`, run Alembic.
3. Keep same API, SQL schema, query layer, worker jobs. Replace filesystem for big content with object storage if volume grows; only storage adapter changes.
4. If moving to Qdrant later, implement `VectorIndex` port; batch backfill embeddings. **No promise of zero work**, but no core domain rewrite.
