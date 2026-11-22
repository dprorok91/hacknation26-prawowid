"""Deterministic request -> persisted job -> retrieval -> checked citation -> API report."""

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.application.worker import JobWorker
from prawdowid.infrastructure.analysis_store import (
    ANALYZE,
    PostgresAnalyses,
    claim_analysis_input,
    finish_analysis,
)
from prawdowid.infrastructure.corpus import ArticleSnapshot, digest, store_article
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.db_models import (
    analyses,
    audit_events,
    claims,
    documents,
    evidence,
    jobs,
    sources,
)
from prawdowid.infrastructure.discovery import DiscoveryReport
from prawdowid.infrastructure.ingestion import FETCH_URL, ArticleIngestion, CorpusAccess
from prawdowid.infrastructure.job_queue import PostgresJobQueue
from prawdowid.infrastructure.ollama import OllamaClient
from prawdowid.infrastructure.openrouter import OpenRouterClient
from prawdowid.infrastructure.retrieval import PostgresEvidenceRetriever
from prawdowid.infrastructure.safe_web import SafeWeb
from prawdowid.main import create_app
from prawdowid.worker import select_analysis_model


class EmptyDiscovery:
    async def discover(self, event):
        pytest.fail("Private text submissions must never reach external discovery")


class PublicDiscovery:
    async def discover(self, event):
        return DiscoveryReport(candidates=(), errors=())


@pytest.mark.asyncio
async def test_evidence_backed_api_and_message_demo_use_same_snapshot():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to a migrated disposable PostgreSQL database.")
    marker = f"Projekt{uuid4().hex[:8]}"
    phrase = f"W Krakowie otwarto most {marker}."
    domain = f"{uuid4().hex}.example"
    url = f"https://{domain}/komunikat"
    now = datetime.now(UTC)
    database = create_database(dsn)
    analysis_ids = []

    hosted_tasks = []

    def respond(request):
        payload = json.loads(request.content)
        prompt = json.loads(payload["messages"][1]["content"])
        if "input_text" in prompt:
            answer = {
                "query_text": f"Krakowie otwarto most {marker}",
                "entities": [marker],
                "claims": [{"text": phrase, "kind": "verifiable"}],
            }
        elif prompt["task"] == "assess":
            answer = {
                "kind": "verifiable", "scope": "public",
                "empirical_test": "Sprawdzić komunikat o otwarciu mostu.",
                "reason": "Otwarcie mostu jest zdarzeniem publicznym.",
            }
        else:
            passage = prompt["passages"][0]
            answer = {
                "citations": [
                    {"passage_id": passage["passage_id"], "quote": phrase, "relation": "supports"}
                ]
            }
        if request.url.host == "openrouter.ai":
            hosted_tasks.append(prompt["task"])
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {
                    "role": "assistant", "content": json.dumps(answer)
                }}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.0002},
            })
        return httpx.Response(
            200,
            json={"done": True, "prompt_eval_count": 4, "eval_count": 2, "message": {"role": "assistant", "content": json.dumps(answer)}},
        )

    try:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.insert(sources).values(
                    domain=domain,
                    display_name="Publisher fixture",
                    source_type="primary",
                    approved_at=now,
                    enabled=True,
                    license_label="fixture",
                )
            )
            await store_article(
                session,
                ArticleSnapshot(
                    url,
                    "Publisher fixture",
                    f"Otwarto most {marker}",
                    now,
                    now,
                    phrase,
                ),
            )
        service = PostgresAnalyses(database.sessions)
        controller = httpx.ASGITransport(app=create_app(service))
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as model_client,
            httpx.AsyncClient(transport=controller, base_url="http://testserver") as client,
        ):
            local = OllamaClient(model_client, "http://ollama", "qwen3:4b")
            hosted = OpenRouterClient(model_client, SecretStr("test-only"), "openai/gpt-4.1-mini")
            pipeline = AnalysisPipeline(
                EvidenceService(local),
                PostgresEvidenceRetriever(database.sessions),
                EmptyDiscovery(),
                CorpusAccess(database.sessions),
            )

            async def handler(lease):
                async with database.sessions.begin() as session:
                    request = await claim_analysis_input(session, lease)
                analysis_id = lease.payload["analysis_id"]
                model, article = await select_analysis_model(request, pipeline.corpus, local, hosted)
                with model.measure_usage() as usage:
                    report = await pipeline.analyze(
                        analysis_id, request, article=article, evidence=EvidenceService(model)
                    )

                async def effect(session):
                    await finish_analysis(
                        session,
                        analysis_id,
                        report=report,
                        model_name="qwen3:4b" if model is local else "openai/gpt-4.1-mini",
                        tokens_in=usage.tokens_in,
                        tokens_out=usage.tokens_out,
                        estimated_cost_usd=usage.cost_usd,
                    )

                return effect

            worker = JobWorker(
                PostgresJobQueue(database.sessions, kinds=(ANALYZE,)), marker, handler
            )
            reports = []
            tokens = []
            for path in ("/api/v1/analyses", "/api/v1/adapters/message-demo"):
                response = await client.post(path, json={"kind": "text", "content": phrase})
                assert response.status_code == 202
                analysis_ids.append(response.json()["analysis_id"])
                tokens.append(response.headers["X-Analysis-Delete-Token"])
                assert await worker.run_once()
                result = await client.get(response.headers["location"])
                assert result.json()["status"] == "completed"
                report = result.json()["report"]
                assert report["claims"][0]["verdict"] == "confirmed"
                assert report["sources"][0]["exact_quote"] == phrase
                assert report["sources"][0]["url"] == url
                reports.append(report)
            assert len(set(analysis_ids)) == 2
            assert reports[0]["source_snapshot"] == reports[1]["source_snapshot"]
            assert reports[0]["claims"] == reports[1]["claims"]
            assert reports[0]["sources"] == reports[1]["sources"]
            assert (
                await client.get(f"/api/v1/public-reports/{analysis_ids[0]}")
            ).status_code == 404
            async with database.sessions() as session:
                assert (
                    await session.scalar(
                        sa.select(sa.func.count())
                        .select_from(evidence.join(claims))
                        .where(claims.c.analysis_id.in_(analysis_ids), evidence.c.validation_passed)
                    )
                    == 2
                )
            private_url = f"/api/v1/analyses/{analysis_ids[0]}"
            audit = await client.get(private_url + "/audit")
            assert audit.json()["tokens_in"] == 12
            assert audit.json()["tokens_out"] == 6
            assert audit.json()["estimated_cost_usd"] == 0
            assert hosted_tasks == []
            assert phrase not in audit.text
            assert (await client.delete(private_url)).status_code == 404
            assert (
                await client.delete(private_url, headers={"X-Analysis-Delete-Token": tokens[1]})
            ).status_code == 404
            assert (
                await client.delete(private_url, headers={"X-Analysis-Delete-Token": tokens[0]})
            ).status_code == 204
            assert (await client.get(private_url)).status_code == 404
            assert (await client.get(private_url + "/audit")).status_code == 404
            pipeline.discovery = PublicDiscovery()
            public = await client.post("/api/v1/analyses", json={"kind": "url", "content": url})
            assert public.status_code == 202
            public_id = public.json()["analysis_id"]
            analysis_ids.append(public_id)

            async def resolve(host, port):
                return ("8.8.8.8",)

            def web_response(request):
                if request.url.path == "/robots.txt":
                    return httpx.Response(404)
                return httpx.Response(
                    200,
                    headers={"Content-Type": "text/html"},
                    text=f"<title>Otwarto most {marker}</title><article><p>{phrase}</p></article>",
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(web_response)) as web:
                fetch = JobWorker(
                    PostgresJobQueue(database.sessions, kinds=(FETCH_URL,)),
                    "fixture-fetch",
                    ArticleIngestion(database.sessions, SafeWeb(web, resolve=resolve)),
                )
                assert await fetch.run_once()
            assert await worker.run_once()
            assert hosted_tasks == ["extract", "assess", "verify"]
            public_audit = (await client.get(public.headers["location"] + "/audit")).json()
            assert public_audit["model_name"] == "openai/gpt-4.1-mini"
            assert public_audit["tokens_in"] == 30
            assert public_audit["estimated_cost_usd"] == pytest.approx(0.0006)
            shared = f"/api/v1/public-reports/{public_id}"
            assert (await client.get(shared)).json()["status"] == "completed"
            async with database.sessions.begin() as session:
                await session.execute(
                    sa.update(sources)
                    .where(sources.c.domain == domain)
                    .values(license_label="Policy changed")
                )
            assert (await client.get(shared)).status_code == 404
            assert (await client.get(public.headers["location"])).json()[
                "error_code"
            ] == "source_unavailable"
    finally:
        async with database.sessions.begin() as session:
            await session.execute(
                sa.delete(jobs).where(jobs.c.dedupe_key.in_([*analysis_ids, digest(url)]))
            )
            await session.execute(
                sa.delete(audit_events).where(audit_events.c.analysis_id.in_(analysis_ids))
            )
            await session.execute(sa.delete(analyses).where(analyses.c.id.in_(analysis_ids)))
            await session.execute(sa.delete(documents).where(documents.c.canonical_url == url))
            await session.execute(sa.delete(sources).where(sources.c.domain == domain))
        await database.dispose()
