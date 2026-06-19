"""Operational readiness: the ``/readyz`` probe.

Liveness and readiness are deliberately split. ``/health`` (in ``app.py``) is
liveness: it answers "is the process up?" and never touches a dependency, so an
orchestrator's liveness check never restarts a pod just because Postgres
hiccuped. ``/readyz`` is readiness: it answers "can this instance actually serve
traffic right now?" by pinging the database and checking that a usable image
backend is configured, returning 200 when both are good and 503 otherwise so a
load balancer drains an instance that has lost a dependency instead of sending it
requests it will only fail.

This surface owns no provider credentials and runs no generation; the backend
check is a cheap ``health()`` (credentials/endpoint present), never a live
provider call, and the database check is a single ``SELECT 1``.
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI, Response
from sqlalchemy import text

from ..config import get_settings

_log = structlog.get_logger("brand_ops.api.lifecycle")

# How long the readiness DB ping may take before we call the database not-ready.
# Short on purpose: a readiness probe must answer fast, and a slow database is
# itself a not-ready signal.
_DB_PING_TIMEOUT_SEC = 3.0


async def _check_database() -> tuple[bool, str]:
    """Run a single ``SELECT 1`` through the configured engine. (ok, detail).

    Returns ``(False, "not_configured")`` when no ``DATABASE_URL`` is set (so a
    keyless local run reports the database as a known-absent dependency rather
    than erroring), and ``(False, <error class>)`` when the ping raises or times
    out. The error detail is the exception class name only, never the message, so
    a connection string or host never leaks into the probe body.
    """
    settings = get_settings()
    if not settings.database_url:
        return False, "not_configured"
    try:
        import asyncio

        from ..db.base import get_sessionmaker

        async def _ping() -> None:
            maker = get_sessionmaker()
            async with maker() as session:
                await session.execute(text("SELECT 1"))

        await asyncio.wait_for(_ping(), timeout=_DB_PING_TIMEOUT_SEC)
        return True, "ok"
    except TimeoutError:
        return False, "timeout"
    except Exception as exc:  # noqa: BLE001 - any DB failure is "not ready", reported by class
        return False, exc.__class__.__name__


def _check_image_backend() -> tuple[bool, str]:
    """Whether at least one usable image backend is configured. (ok, name|reason).

    Checks the configured fallback chain (the gateway first, then OpenRouter, per
    settings) and reports the first backend whose cheap ``health()`` passes. This
    is exactly the "can we generate at all?" question the engine asks before a
    run, lifted to the readiness probe so an instance with no working backend
    drains instead of taking generation requests it will 422. Returns
    ``(False, "none_configured")`` when nothing in the chain is healthy. A backend
    module that fails to import or build is treated as unhealthy, never fatal to
    the probe.
    """
    settings = get_settings()
    from ..backends.base import load_backend

    for name in settings.fallback_image_backends:
        try:
            if load_backend(name, settings).health():
                return True, name
        except Exception:  # noqa: BLE001 - a broken backend is just "not usable"
            continue
    return False, "none_configured"


def setup_lifecycle(app: FastAPI) -> FastAPI:
    """Add the ``/readyz`` readiness route to ``app``. Leaves ``/health`` alone.

    Call once after :func:`create_app`. The route is intentionally not in the
    OpenAPI schema (it is an infra probe, not part of the product API) and never
    requires auth, so a Kubernetes / load-balancer readiness check can reach it
    with no token even when ``AUTH_REQUIRED`` is on.
    """

    @app.get("/readyz", include_in_schema=False, tags=["meta"])
    async def readyz(response: Response) -> dict:
        """Readiness: 200 when the DB pings and an image backend is usable, else 503.

        The body always lists each dependency's status so an operator reading the
        probe sees WHICH dependency is down, not just that the instance is
        not-ready. Distinct from ``/health`` (liveness, no dependencies).
        """
        db_ok, db_detail = await _check_database()
        backend_ok, backend_detail = _check_image_backend()
        ready = db_ok and backend_ok
        if not ready:
            response.status_code = 503
            _log.warning(
                "readiness_not_ready",
                database=db_detail,
                image_backend=backend_detail,
            )
        return {
            "status": "ready" if ready else "not_ready",
            "service": "brand-ops",
            "checks": {
                "database": {"ok": db_ok, "detail": db_detail},
                "image_backend": {"ok": backend_ok, "detail": backend_detail},
            },
        }

    return app


__all__ = ("setup_lifecycle",)
