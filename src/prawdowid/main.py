"""FastAPI app with minimal health endpoint. Remaining handlers are deliberate 501 stubs."""
from fastapi import FastAPI
from prawdowid.api.routes import router

app = FastAPI(title="PRAWDOWID MVP", version="0.0.1", description="Starter only")
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "implementation": "starter-only"}
