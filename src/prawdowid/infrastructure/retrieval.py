"""PostgreSQL full-text and vector retrieval with evidence provenance."""

import asyncio
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prawdowid.domain.models import Claim, EventQuery, EvidencePassage
from prawdowid.domain.ports import Embedder
from prawdowid.infrastructure.db_models import document_versions, documents, passages, sources
from prawdowid.infrastructure.embeddings import EMBEDDING_DIMENSIONS, EmbeddingUnavailable

CANDIDATE_LIMIT = 30
RESULT_LIMIT = 10
MAX_QUERY_TERMS = 32
RRF_BASE = 60
WORDS = re.compile(r"[^\W_]+")
POLISH_STOP_WORDS = frozenset(
    ("czy", "dla", "do", "jak", "jest", "na", "nie", "od", "oraz", "po", "się", "to", "we", "ze")
)


@dataclass(frozen=True)
class RetrievedPassage(EvidencePassage):
    fetched_at: datetime
    content_version: int
    char_start: int
    char_end: int
    source_span_hash: str
    source_domain: str
    source_type: str
    score: float = 0


@dataclass(frozen=True)
class SearchResult:
    passages: tuple[RetrievedPassage, ...] = ()
    warnings: tuple[str, ...] = ()


def _tokens(text: str) -> list[str]:
    return WORDS.findall(unicodedata.normalize("NFC", text).casefold())


def _keywords(text: str) -> list[str]:
    return [word for word in _tokens(text) if len(word) > 1 and word not in POLISH_STOP_WORDS]


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def matches_event(event: EventQuery, hit: RetrievedPassage) -> bool:
    """Require date and entity anchors when present, plus explicit action terms."""
    if event.occurred_after is not None or event.occurred_before is not None:
        if hit.published_at is None:
            return False
        date = _utc(hit.published_at)
        if event.occurred_after is not None and date < _utc(event.occurred_after):
            return False
        if event.occurred_before is not None and date > _utc(event.occurred_before):
            return False
    present = set(_tokens(f"{hit.title} {hit.exact_text}"))
    entities = [set(_keywords(entity)) for entity in event.entities]
    entities = [terms for terms in entities if terms]
    if entities and not any(terms <= present for terms in entities):
        return False
    location = set(_keywords(event.location or ""))
    if location and not location <= present:
        return False

    anchors = location.union(*entities)
    action = {word for word in _keywords(event.query_text) if not word.isdecimal()} - anchors
    return len(present & action) >= min(2, len(action))


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[RetrievedPassage]], *, limit: int = RESULT_LIMIT
) -> tuple[RetrievedPassage, ...]:
    rows: dict[str, RetrievedPassage] = {}
    scores: dict[str, float] = {}
    for ranked in rankings:
        seen = set()
        for rank, candidate in enumerate(ranked, start=1):
            key = candidate.passage_id
            if key in seen:
                continue
            seen.add(key)
            rows.setdefault(key, candidate)
            scores[key] = scores.get(key, 0) + 1 / (RRF_BASE + rank)
    keys = sorted(scores, key=lambda key: (-scores[key], key))
    return tuple(replace(rows[key], score=scores[key]) for key in keys[:limit])


class PostgresEvidenceRetriever:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], embedder: Embedder | None = None
    ):
        self.sessions = sessions
        self.embedder = embedder

    async def retrieve(
        self, event: EventQuery, claims: Sequence[Claim]
    ) -> dict[str, list[EvidencePassage]]:
        results = {}
        for claim in claims:
            results[claim.claim_id] = list((await self.search(event, claim.text)).passages)
        return results

    async def search(
        self, event: EventQuery, query: str, *, limit: int = RESULT_LIMIT
    ) -> SearchResult:
        if limit < 1:
            raise ValueError("retrieval limit must be positive")
        if not query.strip():
            return SearchResult()
        max_results = min(limit, RESULT_LIMIT)
        base = _eligible_passages(event)
        lexical = _lexical_query(base, query)
        vector = await self._query_vector(query)
        if vector is None:
            text_hits = await self._rows(lexical) if lexical is not None else []
            vector_hits = []
            warnings = ("embedding_unavailable",)
        else:
            semantic = _vector_query(base, vector, self.embedder.model_id)
            if lexical is not None:
                text_hits, vector_hits = await asyncio.gather(
                    self._rows(lexical), self._rows(semantic)
                )
            else:
                text_hits, vector_hits = [], await self._rows(semantic)
            warnings = ("lexical_only_no_embeddings",) if text_hits and not vector_hits else ()

        evidence = reciprocal_rank_fusion(
            [
                [hit for hit in text_hits if matches_event(event, hit)],
                [hit for hit in vector_hits if matches_event(event, hit)],
            ],
            limit=max_results,
        )
        return SearchResult(evidence, warnings)

    async def _rows(self, statement) -> list[RetrievedPassage]:
        async with self.sessions() as session:
            result = await session.execute(statement)
            return [RetrievedPassage(**row) for row in result.mappings()]

    async def _query_vector(self, query: str) -> list[float] | None:
        if self.embedder is None:
            return None
        try:
            vectors = await self.embedder.embed_queries([query])
        except EmbeddingUnavailable:
            return None
        if len(vectors) != 1:
            return None
        vector = vectors[0]
        if (
            len(vector) != EMBEDDING_DIMENSIONS
            or not all(isfinite(value) for value in vector)
            or not any(vector)
        ):
            return None
        return vector


def _eligible_passages(event: EventQuery):
    statement = (
        sa.select(
            passages.c.id.label("passage_id"),
            documents.c.id.label("document_id"),
            passages.c.text.label("exact_text"),
            sources.c.display_name.label("publisher"),
            document_versions.c.title,
            documents.c.canonical_url.label("url"),
            document_versions.c.published_at,
            document_versions.c.fetched_at,
            passages.c.content_version,
            passages.c.char_start,
            passages.c.char_end,
            passages.c.source_span_hash,
            sources.c.domain.label("source_domain"),
            sources.c.source_type,
        )
        .select_from(
            passages.join(
                documents,
                sa.and_(
                    documents.c.id == passages.c.document_id,
                    documents.c.content_version == passages.c.content_version,
                ),
            )
            .join(
                document_versions,
                sa.and_(
                    document_versions.c.document_id == passages.c.document_id,
                    document_versions.c.content_version == passages.c.content_version,
                ),
            )
            .join(sources, sources.c.id == documents.c.source_id)
        )
        .where(
            sources.c.enabled.is_(True),
            sources.c.approved_at.is_not(None),
            document_versions.c.text_license == sources.c.license_label,
            documents.c.retrieval_status.in_(("ready", "text_only")),
            document_versions.c.retrieval_status.in_(("ready", "text_only")),
            sa.or_(documents.c.expires_at.is_(None), documents.c.expires_at > sa.func.now()),
        )
    )
    if event.occurred_after is not None:
        statement = statement.where(document_versions.c.published_at >= _utc(event.occurred_after))
    if event.occurred_before is not None:
        statement = statement.where(document_versions.c.published_at <= _utc(event.occurred_before))
    return statement


def _lexical_query(base, query: str):
    terms = list(dict.fromkeys(_keywords(query)))[:MAX_QUERY_TERMS]
    if not terms:
        return None
    config = sa.literal_column("'simple'")
    index_expression = sa.func.to_tsvector(
        config, sa.func.coalesce(passages.c.text, sa.literal_column("''"))
    )
    search_terms = sa.func.to_tsquery(config, " | ".join(terms))
    return (
        base.where(index_expression.bool_op("@@")(search_terms))
        .order_by(sa.func.ts_rank_cd(index_expression, search_terms).desc(), passages.c.id)
        .limit(CANDIDATE_LIMIT)
    )


def _vector_query(base, vector: list[float], model_id: str):
    distance = passages.c.embedding.cosine_distance(vector)
    return (
        base.where(passages.c.embedding_model_id == model_id, passages.c.embedding.is_not(None))
        .order_by(distance, passages.c.id)
        .limit(CANDIDATE_LIMIT)
    )
