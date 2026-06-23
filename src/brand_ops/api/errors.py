"""A single problem+json (RFC 7807) error contract for the REST API.

This is one operational surface over the single generation engine, not a new code
path through it. It owns no provider credentials and never touches a backend or
the database; it only shapes how the FastAPI app the rest of the package builds
reports an error, so every failure (a validation reject, a raised HTTPException, an
unhandled exception) comes back in one machine-readable envelope.

The envelope is additive by design. The original ``detail`` a route raised is
preserved byte-for-byte and the standard fields are wrapped AROUND it, so an
existing client that reads ``response["detail"]`` keeps working unchanged: a
string detail stays a string, a dict detail stays that dict, and the validation
detail stays the same errors array (also surfaced under ``errors``). The added
fields are the RFC 7807 ``type``/``title``/``status`` plus a short machine
``code`` and the request's correlation ``request_id``.

The content type is ``application/problem+json`` so a caller can branch on it. An
unhandled exception is logged with its traceback via the ``brand_ops`` logger but
its text is never placed in the response body, so an internal error leaks nothing
about the stack to the caller.

Wired in ``app.py`` via :func:`register_error_handlers`; the routes, the auth
dependency, and the observability middleware are untouched.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

# RFC 7807 problem documents use this media type so a caller can branch on it.
PROBLEM_JSON = "application/problem+json"

# The HTTP reason phrases used for the ``title`` field, by status code. Kept here
# so the title is stable regardless of the running server's phrase table; an
# unknown code falls back to a generic phrase rather than raising.
_REASON_PHRASES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}

_log = logging.getLogger("brand_ops")


def _reason_phrase(status_code: int) -> str:
    """The HTTP reason phrase for ``status_code`` (``title``), with a safe fallback."""
    return _REASON_PHRASES.get(status_code, "Error")


def _request_id(request: Request) -> str | None:
    """The correlation id for this request, cheaply and without raising.

    Prefers whatever the observability middleware stashed on ``request.state``
    (the id it minted or propagated), then the inbound ``X-Request-ID`` header
    when an error short-circuits before that middleware ran, else None.
    """
    rid = getattr(request.state, "request_id", None)
    if isinstance(rid, str) and rid:
        return rid
    header = request.headers.get("X-Request-ID")
    return header or None


def _problem(
    *,
    status_code: int,
    detail: object,
    code: str,
    request: Request,
    extra: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build one problem+json response, preserving ``detail`` verbatim.

    The standard RFC 7807 fields are wrapped around the original ``detail`` (never
    replacing it), with the short machine ``code`` and the request's correlation
    id added. ``extra`` carries any handler-specific fields (e.g. the validation
    ``errors`` array). ``headers`` re-emits any headers the original error carried.
    """
    body: dict[str, object] = {
        "type": "about:blank",
        "title": _reason_phrase(status_code),
        "status": status_code,
        "code": code,
        "detail": detail,
        "request_id": _request_id(request),
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_JSON,
        headers=headers,
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 for a request that failed schema validation.

    ``detail`` is the same ``exc.errors()`` array FastAPI would have returned, so a
    client that read the validation detail array is unchanged; the same array is
    also surfaced under ``errors`` for a caller that prefers the named field.
    """
    errors = exc.errors()
    return _problem(
        status_code=422,
        detail=errors,
        code="validation_error",
        request=request,
        extra={"errors": errors},
    )


async def _handle_http_exception(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """The envelope for a raised HTTPException (covers fastapi.HTTPException too).

    ``status`` and ``detail`` come straight from the exception, so a route that
    raised a string detail, a dict detail, or any other JSON-able value gets that
    exact value back under ``detail``. Any headers the exception carried (e.g. a
    ``WWW-Authenticate`` on a 401) are re-emitted.
    """
    headers = getattr(exc, "headers", None)
    return _problem(
        status_code=exc.status_code,
        detail=exc.detail,
        code="http_error",
        request=request,
        headers=headers,
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """500 for any otherwise-unhandled exception.

    The real exception (with its traceback) is logged via the ``brand_ops`` logger
    for diagnosis, but only a generic ``detail`` is returned; the exception text
    and stack never reach the response body, so an internal error leaks nothing
    about the server's internals to the caller.
    """
    _log.exception("unhandled_exception", extra={"path": request.url.path})
    return _problem(
        status_code=500,
        detail="Internal Server Error",
        code="internal_error",
        request=request,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the three problem+json handlers on ``app``. Call once in create_app.

    Covers a failed request validation (422), any raised HTTPException (its own
    status; the Starlette base class also catches ``fastapi.HTTPException``), and
    any other unhandled exception (a clean 500 with nothing leaked). Each returns a
    response whose ``detail`` preserves what the error originally carried, with the
    RFC 7807 envelope added around it.
    """
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(Exception, _handle_unexpected_error)


__all__ = (
    "PROBLEM_JSON",
    "register_error_handlers",
)
