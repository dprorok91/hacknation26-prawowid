"""Bare endpoints: intentionally no fake analysis, no database side effects."""
from fastapi import APIRouter, HTTPException
from prawdowid.api.schemas import AnalysisRequest, AnalysisResponse

router = APIRouter(prefix="/api/v1")


@router.post("/analyses", response_model=AnalysisResponse)
async def create_analysis(request: AnalysisRequest) -> AnalysisResponse:
    _ = request
    raise HTTPException(
        status_code=501,
        detail={
            "error_code": "not_implemented",
            "message": "This starter contains contracts only. Implement tasks P0-01..P0-11.",
        },
    )


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(analysis_id: str) -> AnalysisResponse:
    _ = analysis_id
    raise HTTPException(status_code=501, detail={"error_code": "not_implemented"})


@router.post("/adapters/message-demo", response_model=AnalysisResponse)
async def message_demo(request: AnalysisRequest) -> AnalysisResponse:
    return await create_analysis(request)
