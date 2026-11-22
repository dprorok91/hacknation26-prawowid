import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from prawdowid.api.schemas import AnalysisRequest, AnalysisResponse
from prawdowid.main import create_app


def test_text_is_valid_input() -> None:
    result = AnalysisRequest(kind="text", content="ChatGPT Sol")
    assert result.language == "pl"


def test_bad_url_rejected_at_api_boundary() -> None:
    with pytest.raises(ValidationError):
        AnalysisRequest(kind="url", content="file:///etc/passwd")


def test_missing_analysis_status_not_treated_as_verdict() -> None:
    response = AnalysisResponse(analysis_id="example", status="failed", error_code="timeout")
    assert response.report is None


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"kind": "url", "content": "https://"}, 422, "invalid_input"),
        ({"kind": "url", "content": "https://test:pw@example.org/a"}, 422, "invalid_input"),
        ({"kind": "text", "content": "   "}, 422, "invalid_input"),
        ({"kind": "text", "content": "a" * 20001}, 422, "invalid_input"),
        ({"kind": "text", "content": "ChatGPT Sol"}, 422, "needs_clarification"),
        ({"kind": "text", "content": "Warszawa istnieje"}, 503, "source_unavailable"),
        ({"kind": "image", "content": "fixture"}, 415, "unsupported_media"),
        (
            {"kind": "text", "content": "W Warszawie powstała nowa linia."},
            503,
            "source_unavailable",
        ),
    ],
)
def test_submission_errors_are_typed_and_never_verdicts(body, status, code):
    result = TestClient(create_app()).post("/api/v1/analyses", json=body)
    assert result.status_code == status
    assert result.json()["detail"]["error_code"] == code
    assert "verdict" not in result.json()


def test_raw_image_is_unsupported():
    result = TestClient(create_app()).post(
        "/api/v1/analyses", content=b"test image", headers={"Content-Type": "image/png"}
    )
    assert result.status_code == 415
    assert result.json()["detail"]["error_code"] == "unsupported_media"


def test_large_wire_body_is_rejected_early():
    result = TestClient(create_app()).post(
        "/api/v1/analyses",
        content=(b" " * (128 * 1024) for _ in range(3)),
        headers={"Content-Type": "application/json"},
    )
    assert result.status_code == 413
    assert result.json()["detail"]["error_code"] == "invalid_input"


def test_checked_in_request_schema_and_openapi_error_contract():
    path = Path(__file__).parents[1] / "specs/001-core/contracts/analysis-request.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == AnalysisRequest.model_json_schema()
    responses = create_app().openapi()["paths"]["/api/v1/analyses"]["post"]["responses"]
    for status in ("413", "415", "422", "429", "500", "503", "504"):
        assert responses[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


class ExampleAnalyses:
    async def submit(self, request):
        assert request.kind == "url"
        assert request.content == "https://example.org/report"
        return AnalysisResponse(analysis_id="stored", status="queued", retry_after_seconds=2)

    async def get(self, analysis_id):
        if analysis_id == "stored":
            return AnalysisResponse.model_validate(
                {
                    "analysis_id": analysis_id,
                    "status": "completed",
                    "report": {
                        "analysis_id": analysis_id,
                        "sources": [
                            {
                                "source_id": "s1",
                                "publisher": "Test Publisher",
                                "title": "Report",
                                "url": "https://example.org/report",
                            }
                        ],
                    },
                }
            )
        return None


def test_channels_share_submission_and_polling_contract():
    client = TestClient(create_app(ExampleAnalyses()))
    body = {"kind": "url", "content": "https://example.org/report"}
    web = client.post("/api/v1/analyses", json=body)
    demo = client.post("/api/v1/adapters/message-demo", json=body)
    assert web.status_code == demo.status_code == 202
    assert web.json() == demo.json()
    assert web.headers["location"] == "/api/v1/analyses/stored"
    assert client.get(web.headers["location"]).json()["status"] == "completed"
    sources = client.get("/api/v1/analyses/stored/sources")
    assert sources.json()[0]["source_id"] == "s1"
    assert client.get("/api/v1/analyses/absent").status_code == 404


@pytest.mark.parametrize(
    ("route", "body", "eligible_channel"),
    [
        ("analyses", {"kind": "url", "content": "https://example.org/report"}, True),
        ("adapters/message-demo", {"kind": "url", "content": "https://example.org/report"}, False),
        ("analyses", {"kind": "url", "content": "https://example.org/report?utm_id=1"}, False),
        ("analyses", {"kind": "url", "content": "https://example.org/report#private"}, False),
        ("analyses", {"kind": "text", "content": "Moje prywatne dane", "web_search": True}, False),
    ],
)
@pytest.mark.asyncio
async def test_hosted_inference_permission_is_server_derived(route, body, eligible_channel):
    import httpx

    class Capture:
        async def submit(self, submission):
            assert submission.public_model_allowed is eligible_channel
            return AnalysisResponse(analysis_id="captured", status="queued")

    transport = httpx.ASGITransport(app=create_app(Capture()))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        path = f"/api/v1/{route}"
        assert (await client.post(path, json=body)).status_code == 202
        injected = await client.post(path, json={**body, "public_model_allowed": True})
        assert injected.status_code == 422
