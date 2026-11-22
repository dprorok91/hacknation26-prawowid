"""Versioned analysis endpoints shared by the web and message channels."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from prawdowid.api.schemas import (
    AnalysisRequest,
    AnalysisResponse,
    AuditResponse,
    ErrorResponse,
    SourceReference,
)
from prawdowid.application.analyses import AnalysisError, AnalysisService, ErrorCode, Submission


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/v1", dependencies=[Depends(no_store)])

ERROR_STATUSES = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.NEEDS_CLARIFICATION: 422,
    ErrorCode.UNSUPPORTED_MEDIA: 415,
    ErrorCode.SOURCE_UNAVAILABLE: 503,
    ErrorCode.PROVIDER_RATE_LIMITED: 429,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ANALYSIS_NOT_READY: 409,
}
SUBMISSION_ERRORS = {code: {"model": ErrorResponse} for code in (413, 415, 422, 429, 500, 503, 504)}
POLL_ERRORS = {code: {"model": ErrorResponse} for code in (404, 409, 422, 500, 503, 504)}


async def analysis_error_response(request: Request, exc: AnalysisError) -> JSONResponse:
    payload = ErrorResponse(detail={"error_code": exc.code, "message": str(exc)})
    return JSONResponse(payload.model_dump(mode="json"), status_code=ERROR_STATUSES[exc.code])


async def validation_error_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    body_kind = exc.body.get("kind") if isinstance(exc.body, dict) else None
    media_type = request.headers.get("content-type", "").lower()
    if (
        isinstance(body_kind, str)
        and body_kind in ("image", "audio", "video", "file")
        or media_type.startswith(("image/", "audio/", "video/", "multipart/"))
    ):
        error = AnalysisError(ErrorCode.UNSUPPORTED_MEDIA, "Provide Polish text or an HTTP(S) URL.")
    else:
        error = AnalysisError(ErrorCode.INVALID_INPUT, "Invalid input. Check the request fields.")
    return await analysis_error_response(request, error)


def analysis_service(request: Request) -> AnalysisService:
    return request.app.state.analyses


Service = Annotated[AnalysisService, Depends(analysis_service)]


@router.post(
    "/analyses", status_code=202, response_model=AnalysisResponse, responses=SUBMISSION_ERRORS
)
async def create_analysis(
    request: AnalysisRequest, response: Response, service: Service
) -> AnalysisResponse:
    public_url = request.kind == "url"
    return await _submit(request, response, service, public_model_allowed=public_url)


@router.post(
    "/adapters/message-demo",
    status_code=202,
    response_model=AnalysisResponse,
    responses=SUBMISSION_ERRORS,
)
async def create_message_analysis(
    request: AnalysisRequest, response: Response, service: Service
) -> AnalysisResponse:
    return await _submit(request, response, service, public_model_allowed=False)


async def _submit(
    request: AnalysisRequest, response: Response, service: Service, *, public_model_allowed: bool
) -> AnalysisResponse:
    words = request.content.split()
    name_only = len(words) <= 2 and all(
        word[0].isupper() and any(char.islower() for char in word) and word.isalpha()
        for word in words
    )
    if request.kind == "text" and name_only:
        raise AnalysisError(
            ErrorCode.NEEDS_CLARIFICATION, "Provide a complete, checkable statement."
        )
    result = await service.submit(
        Submission(
            request.kind.value,
            request.content,
            web_search=request.web_search,
            public_model_allowed=public_model_allowed,
        )
    )
    response.headers["Location"] = f"/api/v1/analyses/{result.analysis_id}"
    if result.retry_after_seconds is not None:
        response.headers["Retry-After"] = str(result.retry_after_seconds)
    if delete_token := getattr(result, "delete_token", None):
        response.headers["X-Analysis-Delete-Token"] = delete_token
    return AnalysisResponse.model_validate(result, from_attributes=True)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse, responses=POLL_ERRORS)
async def get_analysis(analysis_id: str, service: Service) -> AnalysisResponse:
    result = await service.get(analysis_id)
    if result is None:
        raise AnalysisError(ErrorCode.NOT_FOUND, "Analysis was not found.")
    return AnalysisResponse.model_validate(result, from_attributes=True)


@router.get(
    "/analyses/{analysis_id}/sources", response_model=list[SourceReference], responses=POLL_ERRORS
)
async def get_sources(analysis_id: str, service: Service) -> list[SourceReference]:
    result = await get_analysis(analysis_id, service)
    if result.report is None:
        raise AnalysisError(ErrorCode.ANALYSIS_NOT_READY, "Source provenance is unavailable.")
    return result.report.sources


@router.get("/public-reports/{analysis_id}", response_model=AnalysisResponse, responses=POLL_ERRORS)
async def get_public_report(analysis_id: str, service: Service) -> AnalysisResponse:
    result = await service.get_public(analysis_id)
    if result is None:
        raise AnalysisError(ErrorCode.NOT_FOUND, "Public report was not found.")
    return AnalysisResponse.model_validate(result, from_attributes=True)


@router.delete("/analyses/{analysis_id}", status_code=204, responses=POLL_ERRORS)
async def delete_analysis(
    analysis_id: str,
    service: Service,
    token: Annotated[str | None, Header(alias="X-Analysis-Delete-Token")] = None,
) -> Response:
    if not await service.delete(analysis_id, token):
        raise AnalysisError(ErrorCode.NOT_FOUND, "Analysis was not found.")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/analyses/{analysis_id}/audit", response_model=AuditResponse, responses=POLL_ERRORS)
async def get_audit(analysis_id: str, service: Service) -> AuditResponse:
    result = await service.audit(analysis_id)
    if result is None:
        raise AnalysisError(ErrorCode.NOT_FOUND, "Analysis audit was not found.")
    return AuditResponse.model_validate(result, from_attributes=True)
