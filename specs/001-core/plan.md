# Implementation plan: 24-hour vertical slices

Architecture: FastAPI + Pydantic 2; SQLAlchemy 2 async + Alembic; PostgreSQL16/pgvector; local SentenceTransformers E5; Ollama adapter; asyncio+httpx+feedparser discovery; worker same Python package; React/Vite optional plain responsive HTML first; Chrome MV3 optional P1.

1. Define contract and health -> 2. persistence/migrations -> 3. durable workers + concurrent tests -> 4. source discovery, safe fetch and corpus ingestion -> 5. hybrid retrieval -> 6. claim analyzer/verification -> 7. narrative comparison -> 8. report/citation audit -> 9. web+channel adapter -> 10. evaluation/security -> 11. optional extension.

**Quality gate per slice:** updated docs/OpenAPI, offline tests, PostgreSQL integration test where applicable. Prioritize retrieval reliability and no-fabricated citations over attractive UI.

**Do not precompute fake answers for the jury.** Populate 100+ real source records by permitted means; disclose metadata-only rows and coverage limitations. When a required dependency is unavailable, return a system error, not a truth label.
