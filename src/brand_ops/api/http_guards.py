"""Two transport-level guards over the REST surface: security headers + body cap.

These are operational hardening over the single generation engine, not new code
paths through it. Neither owns a provider credential nor touches a backend or the
database; they only wrap the FastAPI app the rest of the package builds.

  * :class:`SecurityHeadersMiddleware` stamps a small, fixed set of defensive
    response headers on EVERY response (a route may override any of them; the
    middleware never clobbers a header a route already set). The Content-Security
    -Policy is the locked-down policy for a JSON API that serves no HTML. HSTS is
    added only over HTTPS (direct or via an ``x-forwarded-proto`` proxy hop) so
    local http dev is untouched.
  * :class:`BodySizeLimitMiddleware` rejects an over-large request body on the
    write methods (POST/PUT/PATCH) by its declared ``Content-Length`` BEFORE the
    body is read, returning a 413 problem+json. The cap is configurable via
    ``BRAND_OPS_MAX_BODY_BYTES`` (default 32 MiB). Reads and the infra probes are
    never limited.

Wired in ``app.py`` via :func:`setup_http_guards`; the routes, the auth
dependency, and the observability middleware are untouched.
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# RFC 7807 media type, matched to the error envelope so the 413 below is shaped
# like every other error the surface returns.
_PROBLEM_JSON = "application/problem+json"

# The defensive headers stamped on every response. Static values, so they are
# defined once. A route that needs a different value (e.g. a download route that
# sets its own Content-Disposition) is honored: the middleware only fills a header
# the response does not already carry.
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # This surface serves JSON, never HTML or embedded resources, so the strictest
    # policy is correct: load nothing, and never be framed.
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

# Two years, with subdomains. Only sent over HTTPS so a browser is never told to
# pin HSTS from a plain-http local dev response.
_HSTS_VALUE = "max-age=63072000; includeSubDomains"

# Write methods whose body is size-checked. A GET/HEAD (and the infra probes) are
# never limited.
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Paths exempt from the body cap regardless of method, so a probe is never blocked.
_BODY_EXEMPT_PATHS = frozenset({"/health", "/readyz", "/metrics"})

# Default maximum request body, 32 MiB. Overridden by BRAND_OPS_MAX_BODY_BYTES.
_DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024


def _max_body_bytes() -> int:
    """The configured maximum request body in bytes (``BRAND_OPS_MAX_BODY_BYTES``).

    Read once at middleware construction. A missing, empty, non-integer, or
    non-positive value falls back to the 32 MiB default rather than raising, so a
    typo in the deploy env degrades to a sane cap instead of crash-looping boot.
    """
    raw = os.getenv("BRAND_OPS_MAX_BODY_BYTES", "").strip()
    if not raw:
        return _DEFAULT_MAX_BODY_BYTES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_MAX_BODY_BYTES
    return value if value > 0 else _DEFAULT_MAX_BODY_BYTES


def _is_https(request: Request) -> bool:
    """Whether the request reached the surface over HTTPS, directly or via a proxy.

    A direct TLS connection sets ``request.url.scheme``; behind a TLS-terminating
    proxy the original scheme is carried in ``x-forwarded-proto``. Either being
    https means a browser is on a secure origin, so HSTS is safe to assert.
    """
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").strip().lower() == "https"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Stamp the fixed defensive headers on every response without clobbering routes.

    For each header in :data:`_SECURITY_HEADERS` it fills the value only when the
    response does not already carry that header, so a route that deliberately set
    its own (e.g. a stricter Content-Disposition on a download) is never
    overridden. HSTS is added only on a secure origin (see :func:`_is_https`).
    """

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            if name not in response.headers:
                response.headers[name] = value
        if _is_https(request) and "Strict-Transport-Security" not in response.headers:
            response.headers["Strict-Transport-Security"] = _HSTS_VALUE
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject an over-large request body by its Content-Length before it is read.

    Only the write methods (POST/PUT/PATCH) are checked, and the infra probes are
    always exempt. When the declared ``Content-Length`` exceeds the configured cap
    the request is refused with a 413 problem+json and the route never runs, so an
    oversized upload is dropped before any of it is buffered. A request without a
    Content-Length (e.g. a streamed/chunked body) is passed through; the per-route
    decode/size checks remain the backstop for those.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self._max_bytes = _max_body_bytes()

    async def dispatch(self, request: Request, call_next):
        if request.method in _BODY_METHODS and request.url.path not in _BODY_EXEMPT_PATHS:
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    length = int(declared)
                except ValueError:
                    length = -1
                if length > self._max_bytes:
                    return _payload_too_large()
        return await call_next(request)


def _payload_too_large() -> JSONResponse:
    """The 413 problem+json response for an over-large request body."""
    return JSONResponse(
        status_code=413,
        content={
            "type": "about:blank",
            "title": "Payload Too Large",
            "status": 413,
            "detail": "request body too large",
            "code": "body_too_large",
        },
        media_type=_PROBLEM_JSON,
    )


def setup_http_guards(app) -> None:
    """Install both transport guards on ``app``. Call once in ``create_app``.

    Adds the security-headers middleware and the body-size-limit middleware. Both
    are cheap on the request path (a header fill, a single integer compare) and
    read their configuration at construction, matching the rest of the package's
    read-at-build posture, so the wiring in ``app.py`` stays two lines.
    """
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)


__all__ = (
    "BodySizeLimitMiddleware",
    "SecurityHeadersMiddleware",
    "setup_http_guards",
)
