import os
import sys
from datetime import UTC, datetime, timedelta
from types import ModuleType
from uuid import uuid4

import numpy as np
import pytest
import sqlalchemy as sa

from prawdowid.domain.models import Claim, EventQuery
from prawdowid.infrastructure.corpus import ArticleSnapshot, store_article
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import documents, sources
from prawdowid.infrastructure.embeddings import E5Embedder, EmbeddingUnavailable
from prawdowid.infrastructure.retrieval import (
    PostgresEvidenceRetriever,
    RetrievedPassage,
    matches_event,
    reciprocal_rank_fusion,
)

POLISH_CASES = [
    ("Warszawa", "Otwarto trasę"),
    ("Kraków", "Przyjęto uchwałę"),
    ("Łódź", "Ogłoszono remont"),
    ("Poznań", "Uruchomiono linię"),
    ("Wrocław", "Podpisano umowę"),
    ("Gdańsk", "Przebudowano przystanek"),
    ("Szczecin", "Zatwierdzono projekt"),
    ("Lublin", "Przesunięto termin"),
    ("Toruń", "Oddano most"),
    ("Katowice", "Rozpoczęto budowę"),
]


def passage(text, *, published_at=None, passage_id="p", title=""):
    return RetrievedPassage(
        passage_id=passage_id,
        document_id="document",
        exact_text=text,
        publisher="Źródło testowe",
        title=title,
        url="https://example.org/news",
        published_at=published_at,
        fetched_at=datetime.now(UTC),
        content_version=1,
        char_start=4,
        char_end=4 + len(text),
        source_span_hash="test",
        source_domain="example.org",
        source_type="primary",
    )


@pytest.mark.parametrize(("city", "action"), POLISH_CASES)
def test_event_matching_requires_entity_action_and_date(city, action):
    date = datetime(2026, 11, 20, 10, tzinfo=UTC)
    event = EventQuery(
        action,
        entities=(city,),
        occurred_after=date - timedelta(days=1),
        occurred_before=date + timedelta(days=1),
    )
    actual = passage(f"{city}: {action}.", published_at=date)
    assert matches_event(event, actual)
    assert not matches_event(
        event, passage(f"{city}: {action}.", published_at=date + timedelta(days=5))
    )
    assert not matches_event(event, passage(f"Rzeszów: {action}.", published_at=date))
    assert not matches_event(
        event, passage(f"{city}: sprawa pozostała w sądzie.", published_at=date)
    )
    assert not matches_event(event, passage(f"{city}: {action}."))


def test_rrf_promotes_passage_present_in_both_rankings_and_deduplicates():
    first = passage("wynik tekstowy", passage_id="one")
    both = passage("wspólny wynik", passage_id="both")
    semantic = passage("wynik wektorowy", passage_id="semantic")
    result = reciprocal_rank_fusion(([first, both], [semantic, both]), limit=3)
    assert [row.passage_id for row in result] == ["both", "one", "semantic"]
    assert result[0].score == pytest.approx(2 / 62)


@pytest.mark.asyncio
async def test_e5_uses_pinned_local_weights_and_exact_prefixes(monkeypatch):
    state = {"load": [], "encode": []}

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            state["load"].append((model_name, kwargs))

        def encode(self, texts, **kwargs):
            state["encode"].append((texts, kwargs))
            samples = np.zeros((len(texts), 384), dtype=np.float32)
            samples[:, 1] = 1
            return samples

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    adapter = E5Embedder()
    assert not state["load"]
    assert await adapter.embed_queries([]) == []
    assert not state["load"]

    query = await adapter.embed_queries(["Jak działa?"])
    text = await adapter.embed_passages(["Treść artykułu."])
    assert query == text
    assert len(query[0]) == 384 and query[0][1] == 1
    assert len(state["load"]) == 1
    name, kwargs = state["load"][0]
    assert kwargs["local_files_only"] and not kwargs["trust_remote_code"]
    assert kwargs["model_kwargs"]["use_safetensors"]
    assert adapter.model_id == f"{name}@{kwargs['revision']}"
    assert [texts for texts, _ in state["encode"]] == [
        ["query: Jak działa?"],
        ["passage: Treść artykułu."],
    ]
    assert all(options["normalize_embeddings"] for _, options in state["encode"])


@pytest.mark.asyncio
async def test_uncached_e5_failure_is_typed(monkeypatch):
    class MissingSentenceTransformer:
        def __init__(self, *args, **kwargs):
            raise OSError("local model missing")

    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = MissingSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    with pytest.raises(EmbeddingUnavailable):
        await E5Embedder().embed_queries(["pytanie"])


class FixtureEmbedder:
    model_id = "test-e5@v1"

    async def embed_queries(self, texts):
        return [[1.0, *([0.0] * 383)] for _ in texts]


@pytest.mark.asyncio
async def test_pg_fts_and_vector_retrieval_preserve_licensed_current_passages():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")

    database = create_database(dsn)
    marker = f"tenz{uuid4().hex}"
    public_domain = f"{marker}.example"
    other_domain = f"other-{marker}.example"
    disabled_domain = f"disabled-{marker}.example"
    domains = [public_domain, other_domain, disabled_domain]
    now = datetime.now(UTC)
    embedder = FixtureEmbedder()
    query_vector = (await embedder.embed_queries(["test"]))[0]

    async def save(
        domain, path, body, *, date=now, saved=now, model=embedder.model_id, embed=False
    ):
        async with database.sessions.begin() as session:
            return await store_article(
                session,
                ArticleSnapshot(f"https://{domain}/{path}", domain, "Raport", date, saved, body),
                vectors=[query_vector] if embed else None,
                model_id=model if embed else None,
            )

    try:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.insert(sources),
                [
                    {
                        "domain": domain,
                        "display_name": domain,
                        "source_type": "primary",
                        "enabled": True,
                        "approved_at": now,
                        "license_label": "fixture",
                    }
                    for domain in domains
                ],
            )
        for idx, (city, action) in enumerate(POLISH_CASES):
            await save(public_domain, f"case-{idx}", f"{city}: {action}. Komunikat {marker}.")
        old_body = f"  Łódź: Ogłoszono remont. Stara wersja {marker}."
        new_body = f"  Łódź: Ogłoszono remont. Aktualna wersja {marker}."
        await save(public_domain, "versioned", old_body, embed=True)
        await save(
            public_domain, "versioned", new_body, saved=now + timedelta(seconds=1), embed=True
        )
        await save(
            public_domain,
            "other-event",
            f"Łódź: Ogłoszono remont. Inny dzień {marker}.",
            date=now - timedelta(days=3),
            embed=True,
        )
        await save(
            other_domain,
            "other-model",
            "Łódź: Ogłoszono remont. Odrębny model.",
            model="test-e5@v0",
            embed=True,
        )
        await save(
            disabled_domain,
            "revoked",
            f"Łódź: Ogłoszono remont. Cofnięto zgodę {marker}.",
            embed=True,
        )
        async with database.sessions.begin() as session:
            await session.execute(
                sa.update(sources).where(sources.c.domain == disabled_domain).values(enabled=False)
            )

        retriever = PostgresEvidenceRetriever(database.sessions, embedder)
        for idx, (city, action) in enumerate(POLISH_CASES):
            event = EventQuery(action, entities=(city,), occurred_after=now - timedelta(days=1))
            report = await retriever.search(event, f"Komunikat {marker}")
            assert f"https://{public_domain}/case-{idx}" in {row.url for row in report.passages}

        event = EventQuery(
            "Ogłoszono remont", entities=("Łódź",), occurred_after=now - timedelta(days=1)
        )
        report = await retriever.search(event, f"Aktualna {marker}")
        selected = next(row for row in report.passages if row.url.endswith("/versioned"))
        assert selected.content_version == 2
        assert selected.exact_text == new_body[selected.char_start : selected.char_end]
        assert selected.source_domain == public_domain
        assert all("Stara wersja" not in row.exact_text for row in report.passages)
        assert all(
            not row.url.endswith(("/other-event", "/revoked", "/other-model"))
            for row in report.passages
        )

        fallback = PostgresEvidenceRetriever(database.sessions)
        report = await fallback.search(event, f"Aktualna {marker}")
        assert "embedding_unavailable" in report.warnings
        assert any(row.url.endswith("/versioned") for row in report.passages)
        empty = await fallback.search(EventQuery("Niedopasowane wydarzenie"), "nieznanytemat")
        assert empty.passages == ()

        claims = await retriever.retrieve(event, [Claim("claim-1", f"Aktualna {marker}")])
        assert claims["claim-1"]
    finally:
        async with database.sessions.begin() as session:
            source_ids = sa.select(sources.c.id).where(sources.c.domain.in_(domains))
            await session.execute(sa.delete(documents).where(documents.c.source_id.in_(source_ids)))
            await session.execute(sa.delete(sources).where(sources.c.domain.in_(domains)))
        await database.dispose()
