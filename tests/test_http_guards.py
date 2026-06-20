"""Tests for the API-hardening middleware cluster.

These exercise the FastAPI surface with the in-process TestClient and no network:
auth is off by default (AUTH_REQUIRED unset), so every request resolves to the
anonymous principal and no token or Keycloak is needed. They cover the three
hardening pieces wired in app.py:

  * the problem+json (RFC 7807) error envelope, asserting it ADDS the standard
    fields while PRESERVING the original ``detail`` (the backward-compat contract);
  * the defensive security headers stamped on every response;
  * the request-body size cap (a 413 before the body is read) and the new
    per-field length caps on the request models (a clean 422).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Make auth-off explicit and deterministic regardless of the ambient env, so
    # every request is the anonymous principal (the original global view) and the
    # docs stay on (the dev/test profile).
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# problem+json envelope (RFC 7807), with the detail preserved
# ---------------------------------------------------------------------------


def test_404_carries_problem_envelope_and_preserves_detail(client: TestClient) -> None:
    # An unknown brand is a 404 raised as an HTTPException with a string detail.
    # The envelope must add type/title/status/code/request_id AND keep the original
    # string detail byte-for-byte so an existing client that reads it is unchanged.
    resp = client.get("/api/brands/no-such-brand")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["code"] == "http_error"
    assert "request_id" in body
    # Backward-compat: the original string detail is still present and unchanged
    # (passed through verbatim, not replaced by the envelope's generic title).
    assert isinstance(body["detail"], str)
    assert body["detail"].startswith("brand 'no-such-brand' not found")


def test_http_exception_with_dict_detail_is_preserved_verbatim(client: TestClient) -> None:
    # An unknown asset type on upload raises HTTPException(detail={...}). The
    # envelope must preserve the dict detail exactly (so resp["detail"]["error"]
    # keeps resolving), not flatten or stringify it.
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "not-a-type"},
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], dict)
    assert body["detail"]["error"] == "unknown_asset_type"
    # The envelope is still wrapped around it.
    assert body["status"] == 422
    assert body["code"] == "http_error"


def test_validation_error_keeps_detail_array_and_adds_errors(client: TestClient) -> None:
    # A body that violates the schema (missing the required ``asset``) is a real
    # RequestValidationError. ``detail`` must stay the FastAPI errors ARRAY (no
    # existing test asserts its inner shape, but it must remain an array), and the
    # same array is also surfaced under ``errors`` with the validation code.
    resp = client.post("/api/prompt-preview", json={"brand": "magic-unicorn"})
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body["detail"], list)
    assert body["code"] == "validation_error"
    assert isinstance(body["errors"], list)
    assert body["detail"] == body["errors"]


def test_request_id_header_is_echoed_into_the_envelope(client: TestClient) -> None:
    # The envelope's request_id reflects an inbound X-Request-ID, so an error a
    # client triggers is correlatable with the id it sent.
    resp = client.get(
        "/api/brands/no-such-brand", headers={"X-Request-ID": "trace-guard-001"}
    )
    assert resp.status_code == 404
    assert resp.json()["request_id"] == "trace-guard-001"


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_present_on_a_normal_response(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    csp = resp.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_security_headers_present_on_an_error_response(client: TestClient) -> None:
    # The headers wrap the whole surface, so even a 404 problem+json carries them.
    resp = client.get("/api/brands/no-such-brand")
    assert resp.status_code == 404
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"]


def test_hsts_absent_on_plain_http_dev(client: TestClient) -> None:
    # The TestClient speaks plain http, so HSTS must NOT be asserted (local dev is
    # untouched); it is added only on a secure origin.
    resp = client.get("/health")
    assert "strict-transport-security" not in resp.headers


def test_hsts_added_when_forwarded_proto_is_https(client: TestClient) -> None:
    # Behind a TLS-terminating proxy the original scheme is x-forwarded-proto;
    # https there means a browser is on a secure origin, so HSTS is added.
    resp = client.get("/health", headers={"x-forwarded-proto": "https"})
    assert resp.headers.get("strict-transport-security") == (
        "max-age=63072000; includeSubDomains"
    )


# ---------------------------------------------------------------------------
# Body size cap + per-field length caps
# ---------------------------------------------------------------------------


def test_oversized_content_length_post_is_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A POST whose declared Content-Length exceeds the cap is refused with a 413
    # problem+json before the body is read. A tiny cap keeps the test cheap.
    monkeypatch.setenv("BRAND_OPS_MAX_BODY_BYTES", "100")
    c = TestClient(create_app())
    # 200 bytes of body, over the 100-byte cap.
    resp = c.post(
        "/api/prompt-preview",
        headers={"content-type": "application/json"},
        content=b"x" * 200,
    )
    assert resp.status_code == 413
    body = resp.json()
    assert body["status"] == 413
    assert body["title"] == "Payload Too Large"
    assert body["code"] == "body_too_large"
    assert body["detail"] == "request body too large"


def test_body_cap_does_not_block_a_normal_post(client: TestClient) -> None:
    # A normal small POST is well under the 32 MiB default and flows through to the
    # route (a dry-run prompt preview, no provider call).
    resp = client.post(
        "/api/prompt-preview",
        json={"brand": "magic-unicorn", "asset": "og_image", "topic": "launch"},
    )
    assert resp.status_code == 200


def test_overlong_free_text_field_is_422(client: TestClient) -> None:
    # The ``topic`` free-text field is capped (~2000); an over-long value is a
    # clean 422 from the request-model validation, not a 500 or a silent accept.
    resp = client.post(
        "/api/prompt-preview",
        json={"brand": "magic-unicorn", "asset": "og_image", "topic": "z" * 9000},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], list)
