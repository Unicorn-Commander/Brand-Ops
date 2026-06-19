"""Observability tests: logging config, request-context middleware, metrics, Sentry.

Network- and DB-free. The logging tests drive ``configure_logging`` and capture
what a structlog logger renders; the middleware/metrics tests run the real
FastAPI app through the in-process TestClient with auth off (so every request is
the anonymous principal and no token is needed). Sentry is exercised only in its
env-gated no-op path so no DSN is ever contacted.
"""
from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from brand_ops.api import observability
from brand_ops.api.app import create_app
from brand_ops.api.observability import (
    REQUEST_ID_HEADER,
    configure_logging,
    record_backend_fallback,
    record_generation,
    setup_observability,
)


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """Let each test reconfigure logging from scratch.

    ``configure_logging`` is intentionally idempotent via a module flag; tests
    that assert on a specific LOG_FORMAT must clear it (and restore the structlog
    defaults afterward) so they do not see a prior test's renderer.
    """
    observability._logging_configured = False
    yield
    observability._logging_configured = False
    structlog.reset_defaults()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    app = create_app()
    setup_observability(app)
    return TestClient(app)


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_is_callable_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()
    assert observability._logging_configured is True
    # A second call must not stack a second root handler.
    before = list(logging.getLogger().handlers)
    configure_logging()
    assert logging.getLogger().handlers == before


def test_configure_logging_emits_json_with_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    configure_logging()

    # Point the single root handler at an in-memory buffer and read what a
    # structlog logger renders through it.
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    monkeypatch.setattr(handler, "stream", buffer, raising=False)

    structlog.get_logger("test.json").info("hello_event", widget="sprocket")
    line = buffer.getvalue().strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["event"] == "hello_event"
    assert payload["level"] == "info"
    assert payload["logger"] == "test.json"
    assert "timestamp" in payload
    assert payload["widget"] == "sprocket"


def test_configure_logging_console_format_is_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "console")
    configure_logging()
    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    monkeypatch.setattr(handler, "stream", buffer, raising=False)

    structlog.get_logger("test.console").info("human_line")
    out = buffer.getvalue()
    assert "human_line" in out
    # The console renderer is not JSON, so the line does not parse as an object.
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip().splitlines()[-1])


def test_log_level_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert observability._log_level() == logging.WARNING
    monkeypatch.setenv("LOG_LEVEL", "definitely-not-a-level")
    # An unknown level degrades to INFO instead of raising.
    assert observability._log_level() == logging.INFO


# ---------------------------------------------------------------------------
# Request-context middleware
# ---------------------------------------------------------------------------


def test_request_id_is_minted_and_returned(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid and len(rid) >= 8


def test_request_id_is_propagated_when_supplied(client: TestClient) -> None:
    resp = client.get("/health", headers={REQUEST_ID_HEADER: "trace-xyz-001"})
    assert resp.headers.get(REQUEST_ID_HEADER) == "trace-xyz-001"


def test_oversized_request_id_is_replaced(client: TestClient) -> None:
    huge = "z" * 5000
    resp = client.get("/health", headers={REQUEST_ID_HEADER: huge})
    returned = resp.headers.get(REQUEST_ID_HEADER)
    # A hostile, oversized id is dropped for a fresh one, not echoed back.
    assert returned != huge
    assert len(returned) <= 200


def test_access_log_emitted_per_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict] = []

    def _capture(logger, method_name, event_dict):
        events.append(dict(event_dict))
        raise structlog.DropEvent

    # Insert a capturing processor so we can assert on the access-log event
    # without depending on the handler's stream.
    structlog.configure(processors=[_capture])
    client.get("/health")
    structlog.reset_defaults()

    access = [e for e in events if e.get("event") == "http_request"]
    assert access, "expected one http_request access log"
    rec = access[-1]
    assert rec["method"] == "GET"
    assert rec["path"] == "/health"
    assert rec["status"] == 200
    assert isinstance(rec["duration_ms"], (int, float))
    assert rec["request_id"]


def test_middleware_binds_workspace_from_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[dict] = []

    def _capture(logger, method_name, event_dict):
        events.append(dict(event_dict))
        raise structlog.DropEvent

    structlog.configure(processors=[_capture])
    client.get("/health", headers={"X-Workspace-Id": "ws-from-header"})
    structlog.reset_defaults()

    access = [e for e in events if e.get("event") == "http_request"]
    assert access and access[-1]["workspace_id"] == "ws-from-header"


# ---------------------------------------------------------------------------
# Prometheus /metrics
# ---------------------------------------------------------------------------


def test_metrics_endpoint_exposed(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


def test_app_counters_increment_and_are_scraped(client: TestClient) -> None:
    record_generation(True)
    record_generation(False)
    record_backend_fallback("litellm", "openrouter")
    body = client.get("/metrics").text
    assert "brand_ops_generation_total" in body
    assert "brand_ops_backend_fallback_total" in body


def test_record_helpers_are_safe_when_counters_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate prometheus_client being unavailable: the helpers must no-op, not raise.
    monkeypatch.setattr(observability, "GENERATION_TOTAL", None)
    monkeypatch.setattr(observability, "BACKEND_FALLBACK_TOTAL", None)
    record_generation(True)
    record_backend_fallback("a", "b")


# ---------------------------------------------------------------------------
# Sentry (env-gated)
# ---------------------------------------------------------------------------


def test_sentry_noop_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    observability._sentry_initialized = False
    assert observability._init_sentry() is False


def test_setup_observability_returns_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    app = create_app()
    assert setup_observability(app) is app
