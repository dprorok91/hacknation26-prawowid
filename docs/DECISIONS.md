# Architecture decisions (ADRs)

- **ADR-001** PostgreSQL 16 + pgvector locally and hosted later. Rejected: SQLite-primary due to multi-writer job coordination; benefit: same schema/queries as managed Postgres.
- **ADR-002** Modular monolith plus separate worker process; rejected: multiple independent microservices and Redis/Kafka for 24h.
- **ADR-003** Durable PostgreSQL job queue via row leases, `SKIP LOCKED`, at-least-once and idempotent upserts; rejected: in-memory asyncio task as only job record.
- **ADR-004** FTS + multilingual E5 384D + RRF, first exact vector scan; HNSW optional after measurements; rejected: embedding whole URL and opaque LLM relevance only.
- **ADR-005** LLM adapters with local Ollama default, hosted optional; never trust LLM to invent a citation.
- **ADR-006** Narrative comparison distinct from evidence verdicts; both positive and negative consequences may coexist; no universal ideological bias label.
- **ADR-007** Source discovery != evidence; snippets only do not establish body claims, metadata-only documents remain excluded from claim evidence.
- **ADR-008** All clients share API contract; extension manual user gesture + context menu and side panel; WWW + demo message adapter satisfy minimum; Chrome extension as optional third channel.
