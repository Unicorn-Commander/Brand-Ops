"""HTTP (Streamable HTTP) transport for the Brand-Ops MCP server.

The MCP server in ``brand_ops.mcp_server`` is stdio by default. This module
mounts the SAME FastMCP instance (the same tools, the same one engine) over HTTP
so a remote MCP client -- or the suite's Ops-Center / Brigade MCP gateway -- can
reach it at ``/mcp`` with a bearer token, instead of only over a local stdio
pipe.

It is OPT-IN via ``MCP_HTTP_ENABLED`` (default off) so the default app and the
test suite are unchanged. Authentication itself is NOT optional: every request to
``/mcp`` must carry a credential the suite verifier accepts (a JWKS-verified
Keycloak uchub / Brigade bearer, or a Brand-Ops personal access token), and the
caller's workspace is resolved from their memberships, so the tools are
tenant-scoped exactly like the REST surface. The ``.well-known`` manifest stays
public for discovery -- it lists tool names and safety hints, no tenant data.

The endpoint owns no provider keys: a tool's generation still flows through the
metered gateway, the same as the REST and stdio paths.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings

from .. import __version__
from ..config import get_settings
from ..mcp_server import mcp
from .mcp_auth import (
    reset_mcp_principal,
    resolve_mcp_principal,
    set_mcp_principal,
)

_log = logging.getLogger("brand_ops.api.mcp_http")

# The loopback names a local client (an MCP inspector on the box, a container
# health check, the test suite) legitimately uses. Kept alongside the configured
# public hostname rather than instead of it: adding the real hostname must not
# take the local path away.
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def mcp_http_enabled() -> bool:
    """Whether the HTTP MCP endpoint is wired. Off by default."""
    return os.getenv("MCP_HTTP_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def mcp_auth_required() -> bool:
    """Always True: ``/mcp`` cannot be served to an unidentified caller.

    This used to read ``MCP_AUTH_REQUIRED`` (default on) and, when switched off,
    served the protocol endpoint with ``set_workspace_scope(None, None)`` -- i.e.
    every tenant's brands, to anyone who could reach the port. On the production
    profile the ``mcp.brand-ops.*`` Traefik router points straight at the backend
    and bypasses oauth2-proxy, so that switch was one env var away from an
    internet-reachable, all-tenant tool surface that can spend on the metered
    gateway.

    The bypass is gone. The function is kept (and pinned by a test) so the
    manifest and any caller reading the posture get a truthful, constant answer,
    and so setting ``MCP_AUTH_REQUIRED=false`` in an old .env silently tightens
    rather than silently opens.
    """
    return True


def _public_endpoint() -> str:
    return os.getenv("MCP_PUBLIC_URL", "").strip() or "https://mcp.brand-ops.magicunicorn.dev/mcp"


def _clean_host(value: str) -> str | None:
    """Normalise one configured hostname, or None if it may not be trusted.

    A bare ``*`` (or any entry carrying a ``*`` that is not the SDK's trailing
    ``:*`` any-port marker) is DROPPED rather than honoured: a wildcard host
    allow-list is exactly the DNS-rebinding hole the protection exists to close,
    so a typo or a copy-pasted "allow everything" can never disable it here.
    """
    host = value.strip().rstrip("/").lower()
    if not host:
        return None
    if "://" in host:
        host = urlsplit(host).netloc or host
    if "*" in host:
        _log.warning(
            "ignoring wildcard MCP allowed-host entry %r: DNS-rebinding "
            "protection is not disabled by configuration",
            value,
        )
        return None
    return host


def _allowed_hosts() -> list[str]:
    """The exact Host header values ``/mcp`` will serve. Never wildcarded.

    Sources, in order: the loopback names, the hostname of ``MCP_PUBLIC_URL``
    (which already has to be right for the manifest to advertise a reachable
    endpoint), and any extra names in ``MCP_ALLOWED_HOSTS`` (comma separated) for
    a node fronted by more than one router name. Each name is emitted both bare
    and with the SDK's ``:*`` any-port marker, so ``Host: mcp.example.dev`` and
    ``Host: mcp.example.dev:8443`` both match while a foreign name matches
    neither.
    """
    names: list[str] = list(_LOOPBACK_HOSTS)
    # Through _clean_host, exactly like the MCP_ALLOWED_HOSTS entries below.
    # It used to be appended raw, so ``MCP_PUBLIC_URL=https://*/mcp`` put ``*``
    # (and ``*:*``) into the allow-list while the same wildcard typed into
    # MCP_ALLOWED_HOSTS was dropped. Not exploitable -- the SDK matches the Host
    # header exactly, plus the ``:*`` any-port suffix, so a literal ``*`` matches
    # only the literal Host ``*`` -- but "a wildcard never reaches the allow-list"
    # has to be true of EVERY source of a name, or it is not an invariant.
    endpoint_host = _clean_host(urlsplit(_public_endpoint()).hostname or "")
    if endpoint_host:
        names.append(endpoint_host)
    for extra in os.getenv("MCP_ALLOWED_HOSTS", "").split(","):
        cleaned = _clean_host(extra)
        if cleaned:
            names.append(cleaned)

    out: list[str] = []
    for name in names:
        for entry in (name, f"{name}:*"):
            if entry not in out:
                out.append(entry)
    return out


def _transport_security() -> TransportSecuritySettings:
    """DNS-rebinding protection, configured for the hostname we actually serve.

    The bug this fixes: ``FastMCP()`` defaults ``host="127.0.0.1"``, and that
    default AUTO-ENABLES rebinding protection with ``allowed_hosts`` limited to
    the loopback names. Behind a real router the Host header is
    ``mcp.brand-ops.magicunicorn.dev``, which matched nothing, so every
    production request died at ``421 Misdirected Request`` -- inside the MCP
    transport, i.e. AFTER our auth wrapper had already accepted the credential.
    It failed closed, so it was never a hole; it did mean the HTTP surface had
    never been exercised past the auth wrapper.

    Protection stays ON. What changes is that the allow-list now contains the
    hostname the deployment is actually reached on, taken from configuration and
    never from the request. Origin is allow-listed for the same names over https
    (and http on loopback) so a browser-based client works without opening the
    endpoint to a page served from anywhere.
    """
    hosts = _allowed_hosts()
    origins: list[str] = []
    for entry in hosts:
        bare = entry[:-2] if entry.endswith(":*") else entry
        schemes = ("http", "https") if bare in _LOOPBACK_HOSTS else ("https",)
        for scheme in schemes:
            for origin in (f"{scheme}://{bare}", f"{scheme}://{bare}:*"):
                if origin not in origins:
                    origins.append(origin)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


# The inner Streamable-HTTP ASGI app, built once. Setting the path to "/" means a
# mount at "/mcp" exposes the protocol at exactly "/mcp" with no doubled segment.
_inner: Any = None


def _ensure_inner() -> Any:
    global _inner
    if _inner is None:
        mcp.settings.streamable_http_path = "/"
        # Read by streamable_http_app() when it lazily builds the session
        # manager, so it must be set BEFORE that call, not after.
        mcp.settings.transport_security = _transport_security()
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

    Unconditional: no credential, no service. The credential is verified with the
    same chain the REST layer uses, the caller's workspace and readable brands are
    resolved from their memberships (never from a header they control), and the
    resulting principal is published for the request so the per-call guard in
    ``mcp_auth`` re-asserts that scope around every individual tool call.

    Both failure modes are 401 with ``WWW-Authenticate: Bearer`` and no detail
    about WHY beyond "missing" vs "not accepted", so this endpoint is not an
    oracle for probing which tokens or workspaces exist.
    """
    inner = _ensure_inner()
    if scope.get("type") != "http":
        await inner(scope, receive, send)
        return

    headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
    raw = headers.get(b"authorization", b"").decode()
    token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""

    if not token:
        await _reject(send, 401, "missing bearer token")
        return
    x_ws = headers.get(b"x-workspace-id", b"").decode() or None
    resolved = await resolve_mcp_principal(token, requested_workspace_id=x_ws)
    if resolved is None:
        await _reject(
            send, 401, "credential not accepted, or no active workspace membership"
        )
        return

    principal_token = set_mcp_principal(resolved)
    try:
        await inner(scope, receive, send)
    finally:
        reset_mcp_principal(principal_token)


def _tool_annotations(tool: Any) -> dict[str, Any]:
    """Project a tool's MCP ToolAnnotations to a compact manifest flag block.

    Only the hints that are set are emitted, so the manifest stays small and a
    consumer (the Ops-Center / Brigade gateway) can read a tool's read-only and
    spend posture without fetching the full tool schema. ``read_only`` and
    ``destructive`` are the two flags routing cares about most; ``title`` is the
    human label.
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {}
    out: dict[str, Any] = {}
    if ann.title:
        out["title"] = ann.title
    if ann.readOnlyHint is not None:
        out["read_only"] = ann.readOnlyHint
    if ann.destructiveHint is not None:
        out["destructive"] = ann.destructiveHint
    if ann.idempotentHint is not None:
        out["idempotent"] = ann.idempotentHint
    return out


async def build_manifest() -> dict[str, Any]:
    """The public ``.well-known/mcps.json`` descriptor for discovery.

    Lists every tool with its annotation flags (read-only, destructive/spend,
    idempotent), every static resource and per-brand resource template, and every
    prompt, so a consumer can discover the full surface and a tool's safety
    posture from the manifest alone.
    """
    tools = await mcp.list_tools()
    resources = await mcp.list_resources()
    templates = await mcp.list_resource_templates()
    prompts = await mcp.list_prompts()
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
            # The durable credential a headless client can hold. Same bearer
            # header, verified against a sha256 hash instead of a JWKS.
            "credential_types": ["jwt", "brand-ops-pat"],
            "pat_prefix": "brand_pat_",
        },
        # The canonical suite feature_key the generation gate actually checks
        # (settings.entitlement_feature_key, env BRAND_OPS_ENTITLEMENT_FEATURE_KEY).
        # This used to be hard-coded to "brand_ops", which is not the key the code
        # reads -- a consumer that pre-flighted a caller's grants against the
        # manifest would have checked the wrong string.
        "entitlement": get_settings().entitlement_feature_key,
        "tools": [
            {
                "name": t.name,
                "description": t.description or "",
                "annotations": _tool_annotations(t),
            }
            for t in tools
        ],
        "resources": [{"uri": str(r.uri), "name": r.name or ""} for r in resources],
        "resource_templates": [
            {"uri_template": t.uriTemplate, "name": t.name or ""} for t in templates
        ],
        "prompts": [
            {"name": p.name, "description": p.description or ""} for p in prompts
        ],
    }


def attach_mcp_http(app: Any) -> None:
    """Mount the MCP HTTP endpoint and the discovery manifest onto ``app``."""
    _ensure_inner()
    app.mount("/mcp", mcp_asgi_app)

    @app.get("/.well-known/mcps.json", tags=["mcp"])
    async def mcps_manifest() -> dict[str, Any]:  # pragma: no cover - thin route
        return await build_manifest()
