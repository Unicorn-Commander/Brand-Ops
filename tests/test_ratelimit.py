"""Rate-limiting tests: the per-caller cap on the spend-bearing POST routes.

Network- and DB-free. Every test builds the real FastAPI app through the
in-process TestClient with auth off (the anonymous principal, no token needed)
and a deliberately tiny ``BRAND_OPS_RATELIMIT`` so a short burst trips the cap.
The limiter runs as middleware OUTSIDE the routes, so a request is counted before
its body is validated: that lets these tests trip the cap with an empty body and
never reach a provider. The app is built per test (inside each fixture, after the
env is set) because the limiter reads its config at construction.
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from brand_ops.api import ratelimit
from brand_ops.api.app import create_app


def _make_client(monkeypatch: pytest.MonkeyPatch, *, limit: str | None, enabled: str | None,
                 ) -> TestClient:
    """Build a TestClient with the limiter env set, then the app, in that order."""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BRAND_OPS_RATELIMIT_REDIS_URL", raising=False)
    if limit is None:
        monkeypatch.delenv("BRAND_OPS_RATELIMIT", raising=False)
    else:
        monkeypatch.setenv("BRAND_OPS_RATELIMIT", limit)
    if enabled is None:
        monkeypatch.delenv("BRAND_OPS_RATELIMIT_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BRAND_OPS_RATELIMIT_ENABLED", enabled)
    return TestClient(create_app())


@pytest.fixture
def tiny_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose limit is 3/minute, enabled, in-memory store."""
    return _make_client(monkeypatch, limit="3/minute", enabled=None)


def _bearer(uc_uid: str) -> dict[str, str]:
    """A header dict with an UNSIGNED bearer carrying ``uc_uid`` (keying only).

    The limiter reads the subject for bucketing without verifying the signature,
    so an unsigned 3-part token is enough to exercise per-caller keying.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload_raw = json.dumps({"uc_uid": uc_uid}).encode()
    payload = base64.urlsafe_b64encode(payload_raw).rstrip(b"=").decode()
    token = f"{header}.{payload}.sig"
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Core behavior: a burst over the limit on a spend route -> 429 + Retry-After
# ---------------------------------------------------------------------------


def test_burst_over_limit_on_spend_route_returns_429(tiny_client: TestClient) -> None:
    # Limit is 3/minute. The 4th POST to a spend route is rejected before the
    # route runs (empty body, so no provider is ever called).
    for _ in range(3):
        resp = tiny_client.post("/api/director/chat", json={})
        assert resp.status_code != 429
    blocked = tiny_client.post("/api/director/chat", json={})
    assert blocked.status_code == 429


def test_429_carries_retry_after_header_and_json_body(tiny_client: TestClient) -> None:
    for _ in range(3):
        tiny_client.post("/api/generate", json={})
    blocked = tiny_client.post("/api/generate", json={})
    assert blocked.status_code == 429
    # A clear Retry-After header, a positive whole-second value.
    assert "Retry-After" in blocked.headers
    assert int(blocked.headers["Retry-After"]) >= 1
    body = blocked.json()
    assert body["error"] == "rate_limited"
    assert body["retry_after"] >= 1
    assert "detail" in body


def test_each_limited_spend_route_can_be_tripped(tiny_client: TestClient) -> None:
    # Every spend-bearing route in the limited set, including the path-
    # parameterized avatar/variants ones, is capped (one burst each; distinct
    # routes share the same caller bucket, so the first route already exhausts it,
    # which is the intended per-caller cap, so we drive each from a fresh caller).
    routes = [
        "/api/generate",
        "/api/generate-card",
        "/api/voices/preview",
        "/api/agents/asset_pack/run",
        "/api/brands/intake",
        "/api/director/chat",
        "/api/brands/acme/agents/iris/avatar",
        "/api/brands/acme/agents/iris/variants",
    ]
    for i, route in enumerate(routes):
        headers = _bearer(f"caller-{i}")
        for _ in range(3):
            ok = tiny_client.post(route, json={}, headers=headers)
            assert ok.status_code != 429, route
        blocked = tiny_client.post(route, json={}, headers=headers)
        assert blocked.status_code == 429, route


# ---------------------------------------------------------------------------
# Reads and infra probes are never limited
# ---------------------------------------------------------------------------


def test_health_is_never_limited(tiny_client: TestClient) -> None:
    # Far more than the limit; /health must always answer 200.
    for _ in range(10):
        resp = tiny_client.get("/health")
        assert resp.status_code == 200


def test_read_route_is_never_limited(tiny_client: TestClient) -> None:
    # GET /api/brands is a read; it must never 429 no matter the burst.
    for _ in range(10):
        resp = tiny_client.get("/api/brands")
        assert resp.status_code == 200


def test_metrics_and_prompt_preview_not_limited(tiny_client: TestClient) -> None:
    # /metrics is an infra probe; prompt-preview is the explicit no-spend dry run.
    # Neither is in the limited set, so a burst never trips them.
    for _ in range(10):
        assert tiny_client.get("/metrics").status_code == 200
    for _ in range(10):
        # prompt-preview validates its body (422 on {}), but is never 429.
        assert tiny_client.post("/api/prompt-preview", json={}).status_code != 429


# ---------------------------------------------------------------------------
# Per-caller keying: distinct callers do not share a bucket
# ---------------------------------------------------------------------------


def test_distinct_callers_have_isolated_buckets(tiny_client: TestClient) -> None:
    # Caller A exhausts its 3/minute budget on a spend route.
    a = _bearer("alice")
    for _ in range(3):
        assert tiny_client.post("/api/director/chat", json={}, headers=a).status_code != 429
    assert tiny_client.post("/api/director/chat", json={}, headers=a).status_code == 429
    # Caller B (a different uc_uid) still has its own full budget.
    b = _bearer("bob")
    for _ in range(3):
        assert tiny_client.post("/api/director/chat", json={}, headers=b).status_code != 429
    assert tiny_client.post("/api/director/chat", json={}, headers=b).status_code == 429


def test_workspace_header_keys_when_no_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, limit="3/minute", enabled=None)
    ws_a = {"X-Workspace-Id": "ws-a"}
    ws_b = {"X-Workspace-Id": "ws-b"}
    for _ in range(3):
        assert client.post("/api/director/chat", json={}, headers=ws_a).status_code != 429
    assert client.post("/api/director/chat", json={}, headers=ws_a).status_code == 429
    # A different workspace is a different bucket.
    assert client.post("/api/director/chat", json={}, headers=ws_b).status_code != 429


# ---------------------------------------------------------------------------
# The enable/disable switch and the default limit
# ---------------------------------------------------------------------------


def test_disable_switch_turns_off_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, limit="3/minute", enabled="false")
    # With the limiter off, a burst far past the limit never 429s.
    for _ in range(10):
        resp = client.post("/api/director/chat", json={})
        assert resp.status_code != 429


@pytest.mark.parametrize("off_value", ["0", "false", "no", "off", "FALSE", "Off"])
def test_disable_switch_accepts_falsey_values(
    monkeypatch: pytest.MonkeyPatch, off_value: str
) -> None:
    client = _make_client(monkeypatch, limit="2/minute", enabled=off_value)
    for _ in range(6):
        assert client.post("/api/director/chat", json={}).status_code != 429


def test_default_limit_is_not_tripped_by_a_small_burst(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no BRAND_OPS_RATELIMIT set, the default is 30/minute, comfortably above
    # a handful of calls: a small burst of distinct hits never 429s.
    client = _make_client(monkeypatch, limit=None, enabled=None)
    for _ in range(10):
        assert client.post("/api/director/chat", json={}).status_code != 429


def test_malformed_limit_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # A garbage limit string disables the cap (fail open), it never blocks traffic.
    client = _make_client(monkeypatch, limit="not-a-limit", enabled=None)
    for _ in range(20):
        assert client.post("/api/director/chat", json={}).status_code != 429


# ---------------------------------------------------------------------------
# Unit coverage of the parser, the key function, and the path matcher
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30/minute", (30, 60)),
        ("5/second", (5, 1)),
        ("100/hour", (100, 3600)),
        ("1/day", (1, 86400)),
        ("10/minutes", (10, 60)),  # plural unit tolerated
        ("  7 / minute ", (7, 60)),  # surrounding whitespace tolerated
    ],
)
def test_parse_limit_valid(raw: str, expected: tuple[int, int]) -> None:
    assert ratelimit.parse_limit(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "  ", None, "30", "abc/minute", "30/fortnight", "0/minute", "-5/minute", "30/"],
)
def test_parse_limit_invalid(raw: str | None) -> None:
    assert ratelimit.parse_limit(raw) is None


def test_matches_limited_covers_the_spend_routes() -> None:
    assert ratelimit.matches_limited("POST", "/api/generate")
    assert ratelimit.matches_limited("POST", "/api/generate-card")
    assert ratelimit.matches_limited("POST", "/api/voices/preview")
    assert ratelimit.matches_limited("POST", "/api/agents/asset_pack/run")
    assert ratelimit.matches_limited("POST", "/api/brands/intake")
    assert ratelimit.matches_limited("POST", "/api/director/chat")
    assert ratelimit.matches_limited("POST", "/api/brands/acme/agents/iris/avatar")
    assert ratelimit.matches_limited("POST", "/api/brands/acme/agents/iris/variants")


def test_matches_limited_excludes_reads_and_neighbors() -> None:
    # Reads and probes are never limited.
    assert not ratelimit.matches_limited("GET", "/health")
    assert not ratelimit.matches_limited("GET", "/readyz")
    assert not ratelimit.matches_limited("GET", "/metrics")
    assert not ratelimit.matches_limited("GET", "/api/brands")
    assert not ratelimit.matches_limited("POST", "/api/prompt-preview")
    # A GET to a path whose POST is limited is itself not limited (method-scoped).
    assert not ratelimit.matches_limited("GET", "/api/generate")
    # The "/api/generate" rule must not swallow "/api/generate-card" via a prefix,
    # nor match an extra trailing segment (fullmatch-anchored).
    assert not ratelimit.matches_limited("POST", "/api/generate/extra")
    # Avatar GET (serving bytes) is a read, never limited; only the POST is.
    assert not ratelimit.matches_limited("GET", "/api/brands/acme/agents/iris/avatar")


def test_client_key_prefers_bearer_then_workspace_then_ip() -> None:
    from starlette.requests import Request

    def _req(headers: dict[str, str], host: str | None = "1.2.3.4") -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        scope = {
            "type": "http",
            "headers": raw,
            "client": (host, 12345) if host else None,
        }
        return Request(scope)

    # Bearer subject wins.
    assert ratelimit.client_key(_req(_bearer("alice"))) == "uid:alice"
    # No bearer -> workspace header.
    assert ratelimit.client_key(_req({"X-Workspace-Id": "ws-1"})) == "ws:ws-1"
    # Neither -> client IP.
    assert ratelimit.client_key(_req({})) == "ip:1.2.3.4"
    # No client at all -> deterministic fallback.
    assert ratelimit.client_key(_req({}, host=None)) == "ip:unknown"
