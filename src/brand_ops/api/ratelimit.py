"""Per-caller rate limiting for the spend-bearing endpoints.

This is one operational guard over the single generation engine, not a new code
path through it. With auth on, an authenticated caller is otherwise unbounded,
and every spend-bearing route ultimately spends real money (a paid image key, a
metered LLM gateway turn, an orator synthesis). That makes an unbounded caller a
cost-exhaustion and denial-of-service risk against the shared keys. This module
caps how often a single caller may hit those routes, returning a clean 429 with a
``Retry-After`` header when the window is exceeded.

Design, by the file-ownership contract (this is wired in ``app.py``; the routes,
the auth dependency, and the observability middleware are untouched):

  * MIDDLEWARE, not per-route decorators. :class:`RateLimitMiddleware` matches the
    request method and path against :data:`LIMITED_PATTERNS` (a small regex set
    that covers the path-parameterized routes too), so ``routes.py`` carries no
    rate-limit code. Reads, ``/health``, ``/readyz``, and ``/metrics`` never
    match, so they are never limited.
  * KEYED BY THE CALLER. :func:`client_key` prefers the authenticated identity:
    the bearer token's ``uc_uid``/``sub`` claim, read WITHOUT verifying the
    signature (this is a bucket label, never an authorization input, exactly like
    the observability middleware's logging labels), then the ``X-Workspace-Id``
    header the GUI switcher sends, then the client IP. Two distinct callers never
    share a bucket.
  * CONFIGURABLE. ``BRAND_OPS_RATELIMIT`` sets the limit (``"<count>/<window>"``,
    default ``"30/minute"``); ``BRAND_OPS_RATELIMIT_ENABLED`` is the on/off switch
    (default on). The default is high enough that normal interactive use and the
    test suite never trip it.
  * STORAGE. In-memory fixed-window counters by default (correct for the
    single-worker uvicorn this ships as). Set ``BRAND_OPS_RATELIMIT_REDIS_URL`` to
    use a Redis backend instead, so several workers or nodes share one budget.
    The in-memory store is per-process: with multiple workers each gets its own
    counters, so the effective limit is per worker until Redis is configured.

Fail-open by construction: a misparsed limit disables the cap with a logged
warning rather than blocking traffic, and a Redis backend that raises on a check
degrades to allowing the request (a limiter must never be the reason the whole
surface goes down).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_log = logging.getLogger("brand_ops.api.ratelimit")

# The spend-bearing POST routes, by method + path. Each ultimately calls a paid
# provider (image backend, the metered LLM gateway, or the orator), so each is
# capped. Path parameters ({name}, {slug}, {agent}) are matched with a segment
# regex so routes.py needs no per-route code. Anchored end-to-end; the leading
# part is the router prefix ("/api"). Reads and the infra probes are absent here
# on purpose, so they are never limited.
_SEG = r"[^/]+"
_LIMITED_SPECS: tuple[tuple[str, str], ...] = (
    ("POST", r"/api/generate"),
    ("POST", r"/api/generate-card"),
    ("POST", r"/api/plan-generation"),
    ("POST", r"/api/voices/preview"),
    ("POST", rf"/api/agents/{_SEG}/run"),
    ("POST", r"/api/brands/intake"),
    ("POST", r"/api/director/chat"),
    ("POST", rf"/api/brands/{_SEG}/agents/{_SEG}/avatar"),
    ("POST", rf"/api/brands/{_SEG}/agents/{_SEG}/variants"),
)

# Compiled (method, pattern) pairs. The pattern is fullmatch-anchored so
# "/api/generate-card" is not matched by the "/api/generate" rule and a trailing
# extra segment does not match a base route.
LIMITED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(rf"^{pattern}$")) for method, pattern in _LIMITED_SPECS
)

# Window names accepted in BRAND_OPS_RATELIMIT, mapped to seconds.
_WINDOW_SECONDS: dict[str, int] = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}

_DEFAULT_LIMIT = "30/minute"


def is_enabled() -> bool:
    """Whether rate limiting is on. Default ON; any explicit falsey value is off.

    Read at request time (via the middleware's construction) so a deploy or a test
    can flip ``BRAND_OPS_RATELIMIT_ENABLED`` without a code change. Recognized
    off values: ``0``, ``false``, ``no``, ``off`` (case-insensitive). Anything
    else, including unset, is on.
    """
    raw = os.getenv("BRAND_OPS_RATELIMIT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def parse_limit(raw: str | None) -> tuple[int, int] | None:
    """Parse ``"<count>/<window>"`` into ``(count, window_seconds)``.

    ``window`` is one of second/minute/hour/day (an optional ``"5/minute"`` style;
    a bare unit means one). Returns None on any malformed value so the caller can
    fail open with a warning rather than raising at request time. A non-positive
    count is treated as malformed (a limit of zero would block everything, which
    is never the intended config; disable with the enable switch instead).
    """
    if not raw or not raw.strip():
        return None
    text = raw.strip().lower()
    if "/" not in text:
        return None
    count_str, _, window_str = text.partition("/")
    count_str = count_str.strip()
    window_str = window_str.strip().rstrip("s") or ""
    seconds = _WINDOW_SECONDS.get(window_str)
    if seconds is None:
        return None
    try:
        count = int(count_str)
    except ValueError:
        return None
    if count <= 0:
        return None
    return count, seconds


def configured_limit() -> tuple[int, int] | None:
    """The active ``(count, window_seconds)`` from ``BRAND_OPS_RATELIMIT``.

    Falls back to :data:`_DEFAULT_LIMIT` (30/minute) when the env var is unset or
    empty. Returns None only when an explicitly set value is malformed, in which
    case the middleware logs once and disables the cap (fail open).
    """
    raw = os.getenv("BRAND_OPS_RATELIMIT")
    if raw is None or not raw.strip():
        return parse_limit(_DEFAULT_LIMIT)
    parsed = parse_limit(raw)
    if parsed is None:
        _log.warning(
            "BRAND_OPS_RATELIMIT=%r is not a valid '<count>/<window>'; "
            "rate limiting disabled",
            raw,
        )
    return parsed


def matches_limited(method: str, path: str) -> bool:
    """Whether this method+path is one of the spend-bearing routes to limit."""
    for limited_method, pattern in LIMITED_PATTERNS:
        if method == limited_method and pattern.match(path):
            return True
    return False


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _subject_from_bearer(authorization: str | None) -> str | None:
    """The token's ``uc_uid``/``sub`` claim, read WITHOUT verifying the signature.

    This is a bucket LABEL, never an authorization input: it only decides which
    counter a request increments, exactly as the observability middleware reads
    identity for a log line without re-running auth. A forged token can at most
    pick which (already-capped) bucket it falls into; it cannot raise its own
    limit, skip the cap, or authenticate. Any parse problem returns None so the
    caller falls back to the workspace header, then the IP.
    """
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    segments = token.split(".")
    if len(segments) != 3:
        return None
    try:
        claims = json.loads(_b64url_decode(segments[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    for claim in ("uc_uid", "sub"):
        value = claims.get(claim)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def client_key(request: Request) -> str:
    """The rate-limit bucket key for this caller.

    Identity order: the bearer token's ``uc_uid``/``sub`` (unverified, label
    only), then the ``X-Workspace-Id`` header the GUI sends, then the client IP.
    Each is prefixed so the spaces can never collide (a uc_uid that looks like an
    IP still lands in the ``uid:`` space). Falls back to ``ip:unknown`` when even
    the peer address is absent, so a request always has a deterministic bucket.
    """
    subject = _subject_from_bearer(request.headers.get("authorization"))
    if subject:
        return f"uid:{subject}"
    workspace = request.headers.get("x-workspace-id", "").strip()
    if workspace:
        return f"ws:{workspace}"
    client = request.client
    if client is not None and client.host:
        return f"ip:{client.host}"
    return "ip:unknown"


class _InMemoryFixedWindow:
    """A process-local fixed-window counter store, keyed by (bucket, window).

    Each (key, window-start) pair holds a count; a request in a new window starts
    a fresh count. Thread-safe under a single lock (the TestClient and a threaded
    uvicorn worker can both call concurrently). Expired windows are pruned
    opportunistically on access so the dict cannot grow without bound. Per
    process: with multiple workers each has its own store, so the effective limit
    is per worker until a shared Redis backend is configured.
    """

    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, count: int, window_seconds: int) -> tuple[bool, int]:
        """Record one hit. Returns (allowed, retry_after_seconds).

        ``retry_after_seconds`` is the whole seconds until the current window
        rolls over; it is meaningful only when the request is blocked, and is at
        least 1 so a client never sees ``Retry-After: 0``.
        """
        now = int(time.time())
        window_start = now - (now % window_seconds)
        with self._lock:
            existing = self._counts.get(key)
            if existing is None or existing[0] != window_start:
                # New window for this key. Prune other keys whose window has
                # rolled over so the map stays bounded.
                self._prune(window_start)
                self._counts[key] = (window_start, 1)
                return True, 0
            current_start, used = existing
            if used >= count:
                retry_after = max(1, (current_start + window_seconds) - now)
                return False, retry_after
            self._counts[key] = (current_start, used + 1)
            return True, 0

    def _prune(self, current_window_start: int) -> None:
        """Drop entries from a window older than the current one. Caller holds lock."""
        stale = [k for k, (start, _) in self._counts.items() if start < current_window_start]
        for k in stale:
            self._counts.pop(k, None)

    def reset(self) -> None:
        """Clear all counters. Test helper; never called in the request path."""
        with self._lock:
            self._counts.clear()


class _RedisFixedWindow:
    """A Redis-backed fixed-window counter for cross-worker / cross-node budgets.

    Used only when ``BRAND_OPS_RATELIMIT_REDIS_URL`` is set. Each (key, window)
    is a Redis counter with a TTL of the window length, so several uvicorn workers
    or several nodes behind a load balancer share one budget per caller. The
    ``redis`` package is imported lazily here so it stays an optional dependency:
    the default in-memory path needs nothing extra. Fail-open: any Redis error on
    a check allows the request (a limiter must not take the surface down when its
    own store is unreachable).
    """

    def __init__(self, url: str) -> None:
        import redis  # imported lazily; optional dependency

        self._client = redis.Redis.from_url(url)

    def hit(self, key: str, count: int, window_seconds: int) -> tuple[bool, int]:
        now = int(time.time())
        window_start = now - (now % window_seconds)
        redis_key = f"brandops:rl:{key}:{window_start}"
        try:
            used = self._client.incr(redis_key)
            if used == 1:
                # First hit in this window: set the TTL so the key self-expires.
                self._client.expire(redis_key, window_seconds)
        except Exception:  # noqa: BLE001 - a limiter never fails the request on its own store
            _log.warning("rate-limit redis check failed; allowing request", exc_info=True)
            return True, 0
        if used > count:
            retry_after = max(1, (window_start + window_seconds) - now)
            return False, retry_after
        return True, 0


def _build_store() -> _InMemoryFixedWindow | _RedisFixedWindow:
    """The counter store: Redis when configured and importable, else in-memory.

    A configured Redis URL whose client cannot be built (the package is missing or
    the URL is bad) logs once and degrades to the in-memory store, so a
    misconfiguration never blocks boot.
    """
    url = os.getenv("BRAND_OPS_RATELIMIT_REDIS_URL", "").strip()
    if url:
        try:
            store = _RedisFixedWindow(url)
            _log.info("rate limiting using the redis backend")
            return store
        except Exception:  # noqa: BLE001 - fall back rather than fail boot
            _log.warning(
                "BRAND_OPS_RATELIMIT_REDIS_URL set but the redis backend could not "
                "be built; falling back to the in-memory store",
                exc_info=True,
            )
    return _InMemoryFixedWindow()


def _too_many_requests(retry_after: int, limit: tuple[int, int]) -> Response:
    """The 429 response: a clear JSON body plus a ``Retry-After`` header."""
    count, window_seconds = limit
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "detail": (
                f"rate limit exceeded: at most {count} request(s) per "
                f"{window_seconds}s on this endpoint. Retry after {retry_after}s."
            ),
            "retry_after": retry_after,
        },
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Cap the spend-bearing routes per caller; pass everything else through.

    Resolves its config once at construction (the limit, whether it is enabled,
    and the counter store) so the per-request path is only a pattern match and a
    counter hit. When disabled, when the limit is malformed (fail open), or when
    the request is not one of the limited routes, it forwards untouched. A limited
    route over its window gets a 429 with a ``Retry-After`` header; the request
    never reaches the route, so no provider is called.

    Ordering note: this can sit either side of the observability middleware. The
    key function reads only headers and the peer address, never anything the auth
    dependency sets, so it does not depend on running after auth. ``app.py`` adds
    it just BEFORE observability, which (because Starlette runs the last-added
    middleware outermost) makes the request-context logger wrap it: a 429 the
    limiter returns flows back out through observability, so it is logged with a
    request id and carries the X-Request-ID header.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._enabled = is_enabled()
        self._limit = configured_limit() if self._enabled else None
        self._store = _build_store() if (self._enabled and self._limit) else None

    async def dispatch(self, request: Request, call_next):
        # Off, or a malformed limit (fail open): never block traffic.
        if not self._enabled or self._limit is None or self._store is None:
            return await call_next(request)
        if not matches_limited(request.method, request.url.path):
            return await call_next(request)
        count, window_seconds = self._limit
        allowed, retry_after = self._store.hit(client_key(request), count, window_seconds)
        if not allowed:
            _log.info(
                "rate_limited",
                extra={"path": request.url.path, "method": request.method},
            )
            return _too_many_requests(retry_after, self._limit)
        return await call_next(request)


def setup_ratelimit(app) -> None:
    """Install :class:`RateLimitMiddleware` on ``app``. Call once in ``create_app``.

    Reads its configuration from the environment at the time of the call (which is
    app construction), matching the rest of the package's read-at-build posture.
    Adding the middleware is unconditional and cheap even when disabled (the
    dispatch short-circuits), so the wiring in ``app.py`` stays a single line and
    the enable switch is honored entirely inside the middleware.
    """
    app.add_middleware(RateLimitMiddleware)


__all__ = (
    "LIMITED_PATTERNS",
    "RateLimitMiddleware",
    "client_key",
    "configured_limit",
    "is_enabled",
    "matches_limited",
    "parse_limit",
    "setup_ratelimit",
)
