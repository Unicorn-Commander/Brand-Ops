"""Observability wiring: structured logging, request context, metrics, Sentry.

This is one of the operational surfaces over the single generation engine, not a
new code path through it. It owns no provider credentials and never touches a
backend or the database; it only instruments the FastAPI app the rest of the
package builds.

Three pieces, each env-gated and safe to leave off:

  1. :func:`configure_logging` points stdlib logging and structlog at one JSON
     (or console) renderer, honoring ``LOG_LEVEL`` and ``LOG_FORMAT``. Called
     once at process start.
  2. :func:`setup_observability` adds a request-context middleware (assign or
     propagate ``X-Request-ID``; bind request_id, workspace_id, uc_uid into the
     structlog contextvars; emit one structured access log per request), installs
     the Prometheus instrumentator and exposes ``/metrics``, and initializes
     Sentry only when ``SENTRY_DSN`` is set.

The middleware reads identity only from what is cheaply at hand on the request
(the ``X-Workspace-Id`` header the GUI switcher already sends, and any uc_uid the
app has stashed on ``request.state``). It never re-runs auth, so it cannot turn
an anonymous request into an authenticated one or vice versa: it is a logging
label, never an authorization input.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import structlog
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_log = structlog.get_logger("brand_ops.api.observability")

# The header that carries (and propagates) the per-request correlation id. A
# caller (or an upstream proxy / the gateway) may set it; otherwise the
# middleware mints one and echoes it back so a client can correlate its log line
# with the server's.
REQUEST_ID_HEADER = "X-Request-ID"

# Module-level guard so configure_logging is idempotent: importing this module,
# building the app twice in tests, or a double call at boot must not stack
# duplicate handlers / processors.
_logging_configured = False
_sentry_initialized = False


def _log_level() -> int:
    """Resolve the numeric log level from ``LOG_LEVEL`` (default INFO).

    An unknown value falls back to INFO rather than raising, so a typo in the
    deploy env degrades to a sane default instead of crash-looping boot.
    """
    name = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def _log_format() -> str:
    """``json`` (default) or ``console`` from ``LOG_FORMAT``; anything else -> json."""
    fmt = os.getenv("LOG_FORMAT", "json").strip().lower()
    return "console" if fmt == "console" else "json"


def configure_logging() -> None:
    """Point stdlib logging and structlog at one renderer. Idempotent.

    structlog is already a dependency but unused; this turns it on as the single
    formatter for the whole process. Both structlog loggers and plain stdlib
    loggers (uvicorn, sqlalchemy, the app's ``logging.getLogger`` calls) flow
    through the same ``ProcessorFormatter`` so every line is one JSON object with
    a level, an ISO-8601 UTC timestamp, and the logger name. ``LOG_FORMAT`` =
    ``console`` swaps the final renderer for a human-readable colored one (local
    dev); the default ``json`` is for shipping to a log pipeline.

    Called once at process start. Re-calling is a no-op so building the app twice
    (tests) never stacks duplicate handlers.
    """
    global _logging_configured
    if _logging_configured:
        return

    level = _log_level()
    use_json = _log_format() == "json"

    # Processors shared by structlog-native loggers and, via ProcessorFormatter,
    # by foreign (stdlib) records. These run before the final renderer.
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    if use_json:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            # Hand the event dict to the stdlib formatter, which appends the
            # renderer; this is what lets one renderer serve both worlds.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs on records that did NOT originate from structlog
        # (uvicorn, sqlalchemy, app logging.* calls) so they get the same level /
        # name / timestamp shape before the shared renderer.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace any handlers a prior basicConfig / library set up so we do not emit
    # each line twice in two formats.
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers on these named loggers; clear them and
    # let propagation carry their records up to our single root handler, so the
    # access/error lines are JSON too and not duplicated.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    _logging_configured = True


def _client_request_id(request: Request) -> str:
    """The inbound ``X-Request-ID`` if present and sane, else a fresh uuid4.

    A client-supplied id is trusted only as an opaque correlation label and is
    length-capped so a hostile header cannot bloat every log line; it is never
    used in a query, a path, or any security decision.
    """
    raw = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if raw and len(raw) <= 200:
        return raw
    return str(uuid.uuid4())


def _request_workspace_id(request: Request) -> str | None:
    """The workspace label for this request, cheaply, without re-running auth.

    Prefers anything the app stashed on ``request.state`` (e.g. a downstream
    dependency may set ``request.state.workspace_id``); falls back to the
    ``X-Workspace-Id`` header the GUI switcher sends. This is a logging label
    only: the real, membership-gated active workspace is resolved in the auth
    dependency, never here.
    """
    state_ws = getattr(request.state, "workspace_id", None)
    if isinstance(state_ws, str) and state_ws.strip():
        return state_ws.strip()
    header_ws = request.headers.get("X-Workspace-Id", "").strip()
    return header_ws or None


def _request_uc_uid(request: Request) -> str | None:
    """The caller id for this request, cheaply, without re-running auth.

    Only honored when the app has already resolved it onto ``request.state``
    (the auth dependency may set ``request.state.uc_uid``). The middleware does
    no token parsing of its own, so an unauthenticated request simply logs no
    uc_uid rather than the middleware appearing to authenticate it.
    """
    state_uid = getattr(request.state, "uc_uid", None)
    if isinstance(state_uid, str) and state_uid.strip():
        return state_uid.strip()
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign/propagate a request id, bind log context, emit one access log.

    For every request it binds ``request_id`` (and, when cheaply available,
    ``workspace_id`` and ``uc_uid``) into the structlog contextvars so EVERY log
    line emitted while handling the request carries them, then logs one
    ``http_request`` event at the end with the method, path, status, and
    duration. The response always carries the ``X-Request-ID`` back. The
    contextvars are cleared in a ``finally`` so a pooled worker never leaks one
    request's identity into the next.
    """

    async def dispatch(self, request: Request, call_next):
        request_id = _client_request_id(request)
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            # uc_uid / workspace_id are bound late: the auth dependency runs
            # inside call_next, so by here it may have stashed them on state.
            workspace_id = _request_workspace_id(request)
            uc_uid = _request_uc_uid(request)
            if workspace_id:
                structlog.contextvars.bind_contextvars(workspace_id=workspace_id)
            if uc_uid:
                structlog.contextvars.bind_contextvars(uc_uid=uc_uid)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _log.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=duration_ms,
                request_id=request_id,
                workspace_id=workspace_id,
                uc_uid=uc_uid,
            )
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _log.exception(
                "http_request_unhandled",
                method=request.method,
                path=request.url.path,
                status=status_code,
                duration_ms=duration_ms,
                request_id=request_id,
            )
            raise
        finally:
            structlog.contextvars.clear_contextvars()


def _init_sentry() -> bool:
    """Initialize Sentry only when ``SENTRY_DSN`` is set. Returns whether it ran.

    A no-op (and not even an import) without a DSN, so the dependency staying
    uninstalled offline never breaks boot. ``SENTRY_ENVIRONMENT`` and
    ``SENTRY_TRACES_SAMPLE_RATE`` tune it; the FastAPI integration is picked up
    automatically by the SDK's auto-enabling integrations.
    """
    global _sentry_initialized
    if _sentry_initialized:
        return True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        _log.warning("sentry_dsn_set_but_sdk_missing")
        return False
    try:
        traces = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or "0")
    except ValueError:
        traces = 0.0
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "").strip() or None,
        traces_sample_rate=traces,
    )
    _sentry_initialized = True
    _log.info("sentry_initialized", traces_sample_rate=traces)
    return True


# App-specific counters. Defined at import (the registry is process-global) and
# incremented from the engine paths via the thin helpers below, so other modules
# never import prometheus_client directly. Guarded so a re-import in tests does
# not raise "Duplicated timeseries" against the default registry.
try:
    from prometheus_client import Counter

    GENERATION_TOTAL = Counter(
        "brand_ops_generation_total",
        "Asset generations by outcome.",
        ["outcome"],
    )
    BACKEND_FALLBACK_TOTAL = Counter(
        "brand_ops_backend_fallback_total",
        "Times the image backend fell back from its default to a healthy one.",
        ["from_backend", "to_backend"],
    )
    _COUNTERS_READY = True
except (ImportError, ValueError):  # ValueError: already registered (re-import)
    GENERATION_TOTAL = None  # type: ignore[assignment]
    BACKEND_FALLBACK_TOTAL = None  # type: ignore[assignment]
    _COUNTERS_READY = False


def record_generation(success: bool) -> None:
    """Count one asset generation by outcome. No-op if metrics are unavailable.

    Exposed for the engine/route layer to call without importing
    prometheus_client; safe to call when the dep is absent (the counter is None).
    """
    if GENERATION_TOTAL is not None:
        GENERATION_TOTAL.labels(outcome="success" if success else "failure").inc()


def record_backend_fallback(from_backend: str, to_backend: str) -> None:
    """Count one backend fallback (default unhealthy -> a healthy one). No-op if absent."""
    if BACKEND_FALLBACK_TOTAL is not None:
        BACKEND_FALLBACK_TOTAL.labels(from_backend=from_backend, to_backend=to_backend).inc()


def setup_observability(app: FastAPI) -> FastAPI:
    """Wire request-context logging, Prometheus ``/metrics``, and Sentry onto ``app``.

    Call this once, right after :func:`create_app` builds the FastAPI instance
    (and after :func:`configure_logging`). It:

      * installs :class:`RequestContextMiddleware`;
      * installs ``prometheus-fastapi-instrumentator`` and exposes ``/metrics``
        (skipped with a logged warning if the package is not installed, so the
        app still boots offline);
      * initializes Sentry only when ``SENTRY_DSN`` is set.

    Safe to call once per app instance: the instrumentator de-dupes its own
    default-registry collectors internally, so building the app more than once in
    a test session does not raise a duplicate-timeseries error, and each app still
    gets its own ``/metrics`` route.
    """
    app.add_middleware(RequestContextMiddleware)

    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator(
            should_group_status_codes=True,
            excluded_handlers=["/metrics", "/health", "/readyz"],
        ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        _log.warning("prometheus_instrumentator_missing_metrics_disabled")

    _init_sentry()
    return app


__all__ = (
    "REQUEST_ID_HEADER",
    "RequestContextMiddleware",
    "configure_logging",
    "record_backend_fallback",
    "record_generation",
    "setup_observability",
)
