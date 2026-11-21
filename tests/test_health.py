from fastapi.testclient import TestClient

from prawdowid.main import app


def test_health() -> None:
    result = TestClient(app).get("/healthz")
    assert result.status_code == 200
    assert result.json() == {"status": "ok", "implementation": "starter-only"}


def test_analysis_is_explicitly_unimplemented_not_faked() -> None:
    result = TestClient(app).post(
        "/api/v1/analyses", json={"kind": "text", "content": "ChatGPT Sol"}
    )
    assert result.status_code == 501
    assert result.json()["detail"]["error_code"] == "not_implemented"
