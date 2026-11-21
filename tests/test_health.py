from fastapi.testclient import TestClient

from prawdowid.main import app


def test_health() -> None:
    result = TestClient(app).get("/healthz")
    assert result.status_code == 200
    assert result.json() == {"status": "ok", "implementation": "prawdowid"}
