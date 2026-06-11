"""HTTP (Streamable HTTP) transport for the Brand-Ops MCP server.

The MCP server in ``brand_ops.mcp_server`` is stdio by default. This module
mounts the SAME FastMCP instance (the same tools, the same one engine) over HTTP
so a remote MCP client -- or the suite's Ops-Center / Brigade MCP gateway -- can
reach it at ``/mcp`` with a bearer token, instead of only over a local stdio
pipe.

It is OPT-IN via ``MCP_HTTP_ENABLED`` (default off) so the default app and the
test suite are unchanged. ``MCP_AUTH_REQUIRED`` (default ON) gates the protocol
endpoint with the same suite identity the REST layer verifies (Keycloak uchub /
Brigade) and stamps the caller's org onto the request, so the tools are
tenant-scoped exactly like the REST surface. The ``.well-known`` manifest stays
public for discovery.

The endpoint owns no provider keys: a tool's generation still flows through the
metered gateway, the same as the REST and stdio paths.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import Any

from .. import __version__
from ..brand_loader import set_current_org
from ..mcp_server import mcp
from .auth import _default_org, authenticate


def mcp_http_enabled() -> bool:
    """Whether the HTTP MCP endpoint is wired. Off by default."""
    return os.getenv("MCP_HTTP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def mcp_auth_required() -> bool:
    """Whether ``/mcp`` requires a valid bearer. ON by default.

    The HTTP tool surface is remote and powerful (it can spend on the metered
    gateway), so it is gated by default even though the dogfood REST/GUI run open
    under their own ``AUTH_REQUIRED`` knob.
    """
    return os.getenv("MCP_AUTH_REQUIRED", "true").strip().lower() in ("1", "true", "yes", "on")


def _public_endpoint() -> str:
    return os.getenv("MCP_PUBLIC_URL", "").strip() or "https://mcp.brand-ops.magicunicorn.dev/mcp"


# The inner Streamable-HTTP ASGI app, built once. Setting the path to "/" means a
# mount at "/mcp" exposes the protocol at exactly "/mcp" with no doubled segment.
_inner: Any = None


def _ensure_inner() -> Any:
    global _inner
    if _inner is None:
        mcp.settings.streamable_http_path = "/"
        _inner = mcp.streamable_http_app()
    return _inner


@asynccontextmanager
async def mcp_lifespan(app: Any):
    """Run the FastMCP StreamableHTTP session manager for the app's lifetime.

    Starlette does not run a mounted sub-app's lifespan, so the session manager
    is started here, on the parent app, exactly once per process.

    Resilient by design: if the session manager fails to start, the REST API
    (which also backs the GUI) still comes up; only the ``/mcp`` stream is
    degraded until the next clean restart. A startup fault here must never take
    the whole service down.
    """
    import logging

    log = logging.getLogger("brand_ops")
    _ensure_inner()
    cm = mcp.session_manager.run()
    try:
        await cm.__aenter__()
    except Exception:
        log.exception("MCP HTTP session manager failed to start; serving REST only")
        yield
        return
    try:
        yield
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            log.exception("MCP HTTP session manager shutdown error")


async def _reject(send, status: int, message: str) -> None:
    body = json.dumps({"error": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def mcp_asgi_app(scope, receive, send) -> None:
    """Auth + tenancy wrapper around the Streamable-HTTP MCP app.

    Verifies the bearer (when required) with the suite verifier and stamps the
    caller's org onto the request before delegating to the MCP protocol app.
    """
    inner = _ensure_inner()
    if scope.get("type") != "http":
        await inner(scope, receive, send)
        return

    headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
    raw = headers.get(b"authorization", b"").decode()
    token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""

    if mcp_auth_required():
        if not token:
            await _reject(send, 401, "missing bearer token")
            return
        principal = await authenticate(token)
        if principal is None:
            await _reject(send, 401, "invalid or untrusted token")
            return
        set_current_org(principal.org)
    else:
        set_current_org(_default_org())

    await inner(scope, receive, send)


async def build_manifest() -> dict[str, Any]:
    """The public ``.well-known/mcps.json`` descriptor for discovery."""
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()
    return {
        "schema": "unicorn-suite/mcp-manifest@1",
        "name": "brand-ops",
        "display_name": "Brand-Ops",
        "description": (
            "Brand identity and on-brand AI asset generation for the "
            "Unicorn Commander suite."
        ),
        "version": __version__,
        "transport": "streamable-http",
        "endpoint": _public_endpoint(),
        "auth": {
            "type": "bearer",
            "required": mcp_auth_required(),
            "issuers": ["keycloak-uchub", "brigade"],
        },
        "entitlement": "brand_ops",
        "tools": [{"name": t.name, "description": t.description or ""} for t in tools],
        "resources": [{"uri": str(r.uri), "name": r.name or ""} for r in resources],
    }


def attach_mcp_http(app: Any) -> None:
    """Mount the MCP HTTP endpoint and the discovery manifest onto ``app``."""
    _ensure_inner()
    app.mount("/mcp", mcp_asgi_app)

    @app.get("/.well-known/mcps.json", tags=["mcp"])
    async def mcps_manifest() -> dict[str, Any]:  # pragma: no cover - thin route
        return await build_manifest()
