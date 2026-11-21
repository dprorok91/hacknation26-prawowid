"""Durable PRAWDOWID worker entry point."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.application.worker import JobFailure, JobWorker
from prawdowid.config import get_settings
from prawdowid.infrastructure.analysis_store import (
    ANALYZE,
    claim_analysis_input,
    finish_analysis,
    prune_expired,
)
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.discovery import (
    DiscoveryService,
    FactCheckDiscovery,
    GdeltDiscovery,
    GoogleNewsRssDiscovery,
)
from prawdowid.infrastructure.embeddings import E5Embedder
from prawdowid.infrastructure.ingestion import FETCH_URL, ArticleIngestion, CorpusAccess
from prawdowid.infrastructure.job_queue import PostgresJobQueue
from prawdowid.infrastructure.ollama import OllamaClient
from prawdowid.infrastructure.retrieval import PostgresEvidenceRetriever
from prawdowid.infrastructure.safe_web import SafeWeb

logger = logging.getLogger(__name__)


async def mark_analysis_failed(session, lease, error_code):
    if lease.kind == ANALYZE:
        await finish_analysis(session, lease.payload["analysis_id"], error_code=error_code)


async def run(once: bool) -> None:
    settings = get_settings()
    database = create_database(settings.database_url)

    async def process(lease):
        async with database.sessions.begin() as session:
            request = await claim_analysis_input(session, lease)
        if request is None:
            return None
        analysis_id = lease.payload["analysis_id"]
        age = (datetime.now(UTC) - request.queued_at).total_seconds() if request.queued_at else 0
        remaining = settings.analysis_deadline_seconds - max(0, age)
        if remaining <= 0:
            raise JobFailure(ErrorCode.TIMEOUT, retryable=False)
        async with asyncio.timeout(remaining):
            with model.measure_usage() as usage:
                report = await pipeline.analyze(analysis_id, request)

        async def persist(session):
            await finish_analysis(
                session,
                analysis_id,
                report=report,
                model_name=settings.llm_model,
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
            )

        return persist

    async def handle(lease):
        try:
            return await process(lease)
        except TimeoutError:
            raise JobFailure(ErrorCode.TIMEOUT, retryable=False) from None
        except JobFailure:
            raise
        except AnalysisError as exc:
            raise JobFailure(
                exc.code,
                retryable=exc.code in (ErrorCode.TIMEOUT, ErrorCode.PROVIDER_RATE_LIMITED),
            ) from None
        except (KeyError, OSError, RuntimeError, SQLAlchemyError, ValueError) as exc:
            logger.error("Analysis handler failed (%s)", type(exc).__name__)
            raise JobFailure(ErrorCode.INTERNAL_ERROR) from None

    async def poll(worker):
        while True:
            if not await worker.run_once():
                await asyncio.sleep(0.5)

    async def cleanup():
        while True:
            async with database.sessions.begin() as session:
                more = await prune_expired(session)
            await asyncio.sleep(0.1 if more else 60)

    try:
        async with (
            httpx.AsyncClient(
                trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)
            ) as web_client,
            httpx.AsyncClient(trust_env=False) as api_client,
        ):
            embeddings = E5Embedder(settings.embedding_model, revision=settings.embedding_revision)
            providers = [GoogleNewsRssDiscovery(api_client), GdeltDiscovery(api_client)]
            if settings.factcheck_api_key is not None:
                providers.append(
                    FactCheckDiscovery(
                        api_client, api_key=settings.factcheck_api_key.get_secret_value()
                    )
                )
            model = OllamaClient(
                api_client,
                settings.ollama_base_url,
                settings.llm_model,
                timeout_seconds=settings.analysis_deadline_seconds,
                max_concurrency=settings.max_llm_concurrency,
            )
            pipeline = AnalysisPipeline(
                EvidenceService(model),
                PostgresEvidenceRetriever(database.sessions, embeddings),
                DiscoveryService(providers),
                CorpusAccess(database.sessions),
            )
            analyses = PostgresJobQueue(
                database.sessions, kinds=(ANALYZE,), on_dead=mark_analysis_failed
            )
            fetches = PostgresJobQueue(database.sessions, kinds=(FETCH_URL,))
            analysis_worker = JobWorker(analyses, uuid4().hex, handle)
            fetch_worker = JobWorker(
                fetches,
                uuid4().hex,
                ArticleIngestion(database.sessions, SafeWeb(web_client), embeddings),
            )
            if once:
                await fetch_worker.run_once()
                await analysis_worker.run_once()
                return
            async with asyncio.TaskGroup() as group:
                group.create_task(poll(analysis_worker))
                group.create_task(poll(fetch_worker))
                group.create_task(cleanup())
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one available job.")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
