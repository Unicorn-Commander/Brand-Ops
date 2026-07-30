"""Operational lifecycle tests: /readyz readiness, config fail-fast, DB drain.

These cover the Tier-3 operational hardening, distinct from the asset-lifecycle
gate tests in ``test_lifecycle.py``. No live Postgres: the database ping is
exercised by stubbing the sessionmaker so the readiness path is tested with the
DB both "up" (the stub returns) and "down" (the stub raises), and with no DB
configured at all. Auth is off, so the probe needs no token.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops import config
from brand_ops.api.app import create_app
from brand_ops.api.lifecycle import setup_lifecycle
from brand_ops.config import ConfigError, validate_runtime_config
from brand_ops.db import base


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    app = create_app()
    setup_lifecycle(app)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /readyz
# ---------------------------------------------------------------------------


def test_readyz_route_added(client: TestClient) -> None:
    # The probe exists and is reachable without auth.
    resp = client.get("/readyz")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["service"] == "brand-ops"
    assert "database" in body["checks"]
    assert "image_backend" in body["checks"]


def test_readyz_503_when_db_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config.get_settings.cache_clear()
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["ok"] is False
    assert body["checks"]["database"]["detail"] == "not_configured"
    config.get_settings.cache_clear()


def test_readyz_200_when_db_up_and_backend_healthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DB "up": stub the sessionmaker so SELECT 1 succeeds without a real Postgres.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    config.get_settings.cache_clear()

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_a, **_k):
            return None

    def _fake_maker():
        return lambda: _FakeSession()

    monkeypatch.setattr(base, "get_sessionmaker", _fake_maker)

    # Backend "healthy": force one configured backend's health() True.
    import brand_ops.api.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_check_image_backend", lambda: (True, "openrouter"))

    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["image_backend"]["ok"] is True
    config.get_settings.cache_clear()


def test_readyz_503_when_db_ping_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DB "down": the stub raises, so the ping path reports the DB not-ready and
    # the error detail is the exception class name, not its message.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    config.get_settings.cache_clear()

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_a, **_k):
            raise OSError("connection refused: secret-host:5432")

    monkeypatch.setattr(base, "get_sessionmaker", lambda: (lambda: _BrokenSession()))

    import brand_ops.api.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_check_image_backend", lambda: (True, "openrouter"))

    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["checks"]["database"]["ok"] is False
    # The class name surfaces; the connection string / host does not.
    assert body["checks"]["database"]["detail"] == "OSError"
    assert "secret-host" not in resp.text
    config.get_settings.cache_clear()


def test_readyz_not_in_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert "/readyz" not in schema.get("paths", {})


# ---------------------------------------------------------------------------
# validate_runtime_config (config fail-fast)
# ---------------------------------------------------------------------------


def test_validate_config_noop_in_dev_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    # AUTH off => dev/CLI/test profile => never raises, even with nothing set.
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", raising=False)
    config.get_settings.cache_clear()
    settings = validate_runtime_config()
    assert settings is not None
    config.get_settings.cache_clear()


def test_validate_config_raises_in_prod_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", raising=False)
    config.get_settings.cache_clear()
    with pytest.raises(ConfigError) as excinfo:
        validate_runtime_config()
    msg = str(excinfo.value)
    # Both missing keys are named in one message.
    assert "DATABASE_URL" in msg
    assert "BRAND_OPS_BOOTSTRAP_WORKSPACE_ID" in msg
    config.get_settings.cache_clear()


def test_validate_config_passes_in_prod_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv(
        "BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "11111111-1111-1111-1111-111111111111"
    )
    config.get_settings.cache_clear()
    settings = validate_runtime_config()
    assert settings.database_url.endswith("/db")
    config.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Engine pool/timeout config + graceful drain
# ---------------------------------------------------------------------------


def test_connect_args_sets_statement_timeout_for_asyncpg() -> None:
    args = base._connect_args("postgresql+asyncpg://u:p@localhost/db")
    assert args["server_settings"]["statement_timeout"] == str(base._STATEMENT_TIMEOUT_MS)


def test_connect_args_empty_for_non_asyncpg() -> None:
    # A non-asyncpg URL (or no timeout) gets no asyncpg-specific connect args.
    assert base._connect_args("sqlite+aiosqlite:///x.db") == {}


def test_engine_carries_pool_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    config.get_settings.cache_clear()
    base._engine = None
    base._sessionmaker = None
    engine = base.get_engine()
    try:
        # The pool reflects the configured timeout/recycle (no connection opened).
        assert engine.pool._recycle == base._POOL_RECYCLE_SEC
        assert engine.pool._timeout == base._POOL_TIMEOUT_SEC
    finally:
        base._engine = None
        base._sessionmaker = None
        config.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_dispose_engine_is_safe_with_no_engine() -> None:
    # A keyless run never built an engine; disposing must be a quiet no-op.
    base._engine = None
    base._sessionmaker = None
    await base.dispose_engine()
    assert base._engine is None
    assert base._sessionmaker is None


@pytest.mark.asyncio
async def test_shutdown_drains_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    disposed = {"called": False}

    class _FakeEngine:
        async def dispose(self) -> None:
            disposed["called"] = True

    base._engine = _FakeEngine()  # type: ignore[assignment]
    base._sessionmaker = object()  # type: ignore[assignment]
    await base.shutdown()
    assert disposed["called"] is True
    assert base._engine is None
    assert base._sessionmaker is None


@pytest.mark.asyncio
async def test_dispose_engine_swallows_dispose_error() -> None:
    # A failure to drain must be logged, not raised, so shutdown never hangs.
    class _AngryEngine:
        async def dispose(self) -> None:
            raise RuntimeError("pool is wedged")

    base._engine = _AngryEngine()  # type: ignore[assignment]
    base._sessionmaker = object()  # type: ignore[assignment]
    await base.dispose_engine()  # must not raise
    assert base._engine is None
