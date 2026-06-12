"""GET /api/me: the principal as the GUI consumes it."""

from fastapi.testclient import TestClient

from brand_ops.api.app import create_app


def test_me_anonymous_when_auth_off():
    client = TestClient(create_app())
    r = client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["anonymous"] is True
    assert body["org"] == "magic-unicorn"
    assert body["uc_uid"] == "anonymous"
