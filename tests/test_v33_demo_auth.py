"""Fail-closed external-demo authentication and browser-visible access checks."""

import base64

from fastapi.testclient import TestClient
from pydantic import SecretStr

from prawdowid.config import Settings
from prawdowid.main import create_app


def test_demo_basic_auth_protects_ui_and_api():
    settings = Settings(demo_basic_user="tester", demo_basic_password=SecretStr("sixteen-or-more-letters"))
    client = TestClient(create_app(settings=settings))
    assert client.get("/healthz").status_code == 200
    for path in ("/", "/assets/app.js", "/api/v1/analyses/00000000-0000-0000-0000-000000000000", "/docs"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Basic ")
    assert client.get("/", auth=("tester", "wrong")).status_code == 401
    assert client.get("/", auth=("tester", "sixteen-or-more-letters")).status_code == 200


def test_demo_basic_auth_rejects_bad_base64_and_partial_config():
    settings = Settings(demo_basic_user="tester", demo_basic_password=SecretStr("sixteen-or-more-letters"))
    client = TestClient(create_app(settings=settings))
    assert client.get("/", headers={"Authorization": "Basic !!!"}).status_code == 401
    assert client.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"a").decode()}).status_code == 401
    try:
        create_app(settings=Settings(demo_basic_user="tester"))
    except ValueError as exc:
        assert "Configure both" in str(exc)
    else:
        raise AssertionError("Partial demo credentials were accepted")
