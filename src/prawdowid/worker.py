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
from prawdowid.infrastructure.auto_index import INTERVAL_SECONDS, run_cycle
from prawdowid.infrastructure.db import create_database
from prawdowid.infrastructure.discovery import (
    DiscoveryService,
    GdeltDiscovery,
    GoogleNewsRssDiscovery,
    NewsDataDiscovery,
)
from prawdowid.infrastructure.discovery_store import (
    PostgresDailyBudget,
    PostgresDiscoveryCache,
    prune_discovery_cache,
)
from prawdowid.infrastructure.embeddings import E5Embedder
from prawdowid.infrastructure.factchecks import (
    FactCheckLookup,
    GoogleClaimReviewAPI,
    PolishFactcheckRSS,
)
from prawdowid.infrastructure.ingestion import FETCH_URL, ArticleIngestion, CorpusAccess
from prawdowid.infrastructure.job_queue import PostgresJobQueue
from prawdowid.infrastructure.live_official import LiveOfficialEvidence
from prawdowid.infrastructure.ollama import OllamaClient
from prawdowid.infrastructure.openrouter import OpenRouterClient
from prawdowid.infrastructure.retrieval import PostgresEvidenceRetriever
from prawdowid.infrastructure.safe_web import SafeWeb
from prawdowid.infrastructure.search_v2 import (
    ParallelSearch,
    SearchSettings,
    SourceFirstDiscovery,
)

logger = logging.getLogger(__name__)


async def select_analysis_model(request, corpus, local_model, public_model, *, all_inputs=False):
    if all_inputs:
        if public_model is None:
            raise ValueError("All-input routing requires OpenRouter.")
        return public_model, None
    if (
        public_model is None
        or request.kind != "url"
        or request.public_model_allowed is not True
    ):
        return local_model, None
    # CorpusAccess checks current source approval, license, expiry and text availability.
    article = await corpus.article(request.content)
    return public_model, article


async def mark_analysis_failed(session, lease, error_code):
    if lease.kind == ANALYZE:
        await finish_analysis(session, lease.payload["analysis_id"], error_code=error_code)


async def run(once: bool) -> None:
    settings = get_settings()
    public_key = (
        settings.worker_openrouter_key() if settings.public_llm_provider != "ollama" else None
    )
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
            model, article = await select_analysis_model(
                request, corpus, local_model, public_model,
                all_inputs=settings.public_llm_provider == "openrouter_all",
            )
            with model.measure_usage() as usage:
                report = await pipeline.analyze(
                    analysis_id, request, article=article,
                    evidence=EvidenceService(model, public_sources_only=model is public_model)
                )

        async def persist(session):
            await finish_analysis(
                session,
                analysis_id,
                report=report,
                model_name=settings.openrouter_model if model is public_model else settings.llm_model,
                estimated_cost_usd=(
                    None if model is public_model and lease.attempt > 1 else usage.cost_usd
                ),
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
                await prune_discovery_cache(session)
            await asyncio.sleep(0.1 if more else 60)

    async def auto_index(web):
        while True:
            try:
                await run_cycle(database.sessions, web)
            except (KeyError, OSError, RuntimeError, TypeError, ValueError, SQLAlchemyError) as exc:
                # Index maintenance must not terminate the analysis/fetch worker.
                logger.error("Automatic source indexing failed (%s)", type(exc).__name__)
            await asyncio.sleep(INTERVAL_SECONDS)

    try:
        async with (
            httpx.AsyncClient(
                trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)
            ) as web_client,
            httpx.AsyncClient(trust_env=False) as api_client,
            httpx.AsyncClient(trust_env=False, follow_redirects=False) as hosted_client,
        ):
            embeddings = E5Embedder(settings.embedding_model, revision=settings.embedding_revision)
            search_settings = SearchSettings()
            providers = [GoogleNewsRssDiscovery(api_client)]
            if search_settings.enable_gdelt_search:
                providers.append(GdeltDiscovery(api_client))
            parallel = None
            if (
                search_settings.parallel_search_enabled
                and search_settings.parallel_daily_calls > 0
                and search_settings.openrouter_api_key is not None
                and search_settings.openrouter_api_key.get_secret_value().strip()
            ):
                parallel = ParallelSearch(
                    hosted_client,
                    search_settings.openrouter_api_key.get_secret_value(),
                    model=search_settings.parallel_search_model,
                    timeout=search_settings.parallel_timeout_seconds,
                )
            factcheck_providers = [PolishFactcheckRSS(api_client)]
            if settings.factcheck_api_key is not None:
                factcheck_providers.append(
                    GoogleClaimReviewAPI(
                        api_client, settings.factcheck_api_key.get_secret_value()
                    )
                )
            fallback = []
            if settings.enable_newsdata_fallback and settings.newsdata_api_key is not None:
                fallback.append(NewsDataDiscovery(
                    api_client, api_key=settings.newsdata_api_key.get_secret_value()
                ))
            local_model = OllamaClient(
                api_client,
                settings.ollama_base_url,
                settings.llm_model,
                timeout_seconds=settings.analysis_deadline_seconds,
                max_concurrency=settings.max_llm_concurrency,
            )
            public_model = (
                OpenRouterClient(
                    hosted_client, public_key, settings.openrouter_model,
                    timeout_seconds=settings.analysis_deadline_seconds,
                    max_concurrency=settings.max_llm_concurrency,
                ) if public_key is not None else None
            )
            corpus = CorpusAccess(database.sessions)
            pipeline = AnalysisPipeline(
                EvidenceService(local_model),
                PostgresEvidenceRetriever(database.sessions, embeddings),
                SourceFirstDiscovery(
                    DiscoveryService(
                        providers,
                        max_results=30,
                        max_requests_per_window=16,
                        timeout_seconds=5,
                        cache=PostgresDiscoveryCache(
                            database.sessions, hours=settings.discovery_cache_hours
                        ),
                        fallback_providers=fallback,
                        fallback_budget=PostgresDailyBudget(
                            database.sessions, limit=settings.newsdata_daily_credit_budget
                        ) if fallback else None,
                    ),
                    parallel=parallel,
                    cache=PostgresDiscoveryCache(
                        database.sessions, hours=settings.discovery_cache_hours
                    ),
                    budget=PostgresDailyBudget(
                        database.sessions, limit=search_settings.parallel_daily_calls
                    ) if parallel is not None else None,
                ),
                corpus,
                factchecks=FactCheckLookup(tuple(factcheck_providers)),
                live_official=LiveOfficialEvidence(SafeWeb(web_client)),
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
                group.create_task(auto_index(SafeWeb(web_client)))
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
