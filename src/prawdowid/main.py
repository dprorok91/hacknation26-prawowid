"""FastAPI application for the PRAWDOWID analysis service."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from prawdowid.api.body_limit import SubmissionBodyLimit
from prawdowid.api.routes import analysis_error_response, router, validation_error_response
from prawdowid.application.analyses import AnalysisError, AnalysisService, UnavailableAnalyses
from prawdowid.config import Settings, get_settings

WEB = Path(__file__).resolve().parent / "web"
WEB_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def create_app(
    service: AnalysisService | None = None,
    *,
    use_database: bool = False,
    settings: Settings | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None or not use_database:
            yield
            return
        from prawdowid.infrastructure.analysis_store import PostgresAnalyses
        from prawdowid.infrastructure.db import create_database

        runtime = settings or get_settings()
        database = create_database(runtime.database_url)
        app.state.analyses = PostgresAnalyses(
            database.sessions,
            retention_hours=runtime.analysis_retention_hours,
            text_retention_minutes=runtime.text_retention_minutes,
        )
        app.state.database = database
        try:
            yield
        finally:
            await database.dispose()

    app = FastAPI(title="PRAWDOWID MVP", version="0.0.1", lifespan=lifespan)
    app.add_middleware(SubmissionBodyLimit)
    app.state.analyses = service if service is not None else UnavailableAnalyses()
    app.add_exception_handler(RequestValidationError, validation_error_response)
    app.add_exception_handler(AnalysisError, analysis_error_response)
    app.include_router(router)
    app.get("/healthz")(healthz)
    app.get("/", include_in_schema=False)(web_app)
    app.mount("/assets", StaticFiles(directory=WEB, check_dir=False), name="web-assets")
    return app


async def healthz() -> dict[str, str]:
    return {"status": "ok", "implementation": "prawdowid"}


async def web_app() -> FileResponse:
    return FileResponse(WEB / "index.html", headers=WEB_HEADERS)


app = create_app(use_database=True)
