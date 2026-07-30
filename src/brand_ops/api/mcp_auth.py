"""Per-call authentication and tenant scoping for BOTH MCP transports.

Brand-Ops has two MCP surfaces over one FastMCP instance: the HTTP
(Streamable-HTTP) mount at ``/mcp`` and the stdio server
(``python -m brand_ops.mcp_server``). Before this module they had opposite
postures -- the HTTP mount verified a bearer per request, while stdio had no
authentication code path at all and took its tenancy from an environment
variable the operator set on the process. A ``docker exec`` into the backend was
therefore a root-equivalent, all-tenant tool surface.

This module makes the two surfaces share ONE rule:

    no MCP tool call, resource read, or prompt fetch runs without a resolved
    principal and that principal's workspace scope, whatever the transport.

Three pieces:

  1. :func:`resolve_mcp_principal` -- turns a raw credential into a
     :class:`ResolvedPrincipal`. It delegates to the SAME ``api.auth`` chain the
     human REST surface uses (``authenticate`` -> ``_scope_principal``), so the
     MCP door consults the same JWKS verification, the same membership-gated
     tenancy resolver, and the same entitlement resolution. There is no second
     implementation to drift.
  2. A request-scoped principal (:func:`set_mcp_principal`) that the HTTP mount
     publishes, plus a process-scoped credential (:func:`set_stdio_credential`)
     that the stdio entrypoint publishes.
  3. :func:`install_mcp_guard` -- wraps the FastMCP dispatch methods so EVERY
     call re-asserts the principal and re-publishes its scope before the tool
     body runs. That is what makes the enforcement per-call rather than
     per-session, and it is defence in depth behind the ``stateless_http``
     ContextVar-inheritance property the HTTP transport relies on.

Fail-closed everywhere: a missing credential, an unverifiable one, a caller with
no active workspace, a database outage, or any unexpected exception all end in a
refusal. There is no branch that turns an error into an unscoped session.
"""
from __future__ import annotations

import logging
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass

from ..activity import set_current_actor
from ..brand_loader import get_allowed_brands, set_workspace_scope
from ..entitlement import get_current_entitlement, set_entitlement_scope
from . import auth as _auth

_log = logging.getLogger("brand_ops.api.mcp_auth")

__all__ = [
    "MCPAuthError",
    "ResolvedPrincipal",
    "current_call_principal",
    "get_mcp_principal",
    "install_mcp_guard",
    "reset_mcp_principal",
    "resolve_mcp_principal",
    "set_mcp_principal",
    "set_stdio_credential",
]

# How long a stdio session may reuse a resolved principal before re-checking it
# against the database. Bounds how long a revoked token, a removed membership, or
# an expired credential keeps working on a long-lived stdio session; a browser
# session gets the same treatment via the entitlement/tenancy caches.
STDIO_REVALIDATE_SECONDS = 60.0


class MCPAuthError(PermissionError):
    """Raised inside the MCP dispatch when the call carries no valid principal.

    FastMCP turns a raised exception into an MCP error result, so the client sees
    a refusal instead of data. It is a distinct type so a test can assert the
    refusal came from the auth guard and not from a tool's own validation.
    """


@dataclass(frozen=True)
class ResolvedPrincipal:
    """An authenticated MCP caller plus the scope every one of its calls runs in.

    Snapshotting the scope (rather than re-deriving it) lets :meth:`publish` put
    an identical context in place for each individual call, including calls that
    run on a task that did not inherit the resolving context.
    """

    principal: _auth.Principal
    allowed_brands: frozenset[str]
    entitlements: tuple[str, ...] | None

    @property
    def workspace_id(self) -> str | None:
        return self.principal.workspace_id

    @property
    def uc_uid(self) -> str:
        return self.principal.uc_uid

    def publish(self) -> None:
        """Install this principal's tenant scope for the current call.

        Sets the same three request-scoped signals ``api.auth`` sets for a REST
        request -- the brand allow-list, the entitlement grants, and the activity
        actor -- so an MCP tool is confined exactly the way a REST handler is.
        """
        set_workspace_scope(self.principal.workspace_id, self.allowed_brands)
        set_entitlement_scope(
            list(self.entitlements) if self.entitlements is not None else None
        )
        set_current_actor(self.principal.uc_uid)


# The per-request principal. The HTTP mount sets it around the inner ASGI call;
# stdio leaves it unset and falls back to the process credential below.
_current: ContextVar[ResolvedPrincipal | None] = ContextVar(
    "brand_ops_mcp_principal", default=None
)

# The stdio transport's credential, set once by the entrypoint from the
# environment. A process-level global (not a ContextVar) because it belongs to
# the process, not to a call -- but it is only ever a CREDENTIAL, never a
# principal: it is re-verified through the same resolver as everything else.
_stdio_credential: str | None = None
_stdio_requested_workspace: str | None = None
_stdio_cache: tuple[float, ResolvedPrincipal] | None = None


def set_mcp_principal(resolved: ResolvedPrincipal) -> Token:
    """Publish the authenticated caller for this request. Returns the Token."""
    return _current.set(resolved)


def get_mcp_principal() -> ResolvedPrincipal | None:
    """The authenticated caller for this request, or None when unset."""
    return _current.get()


def reset_mcp_principal(token: Token | None = None) -> None:
    """Clear the request-scoped principal (restoring the prior value with a Token)."""
    if token is not None:
        _current.reset(token)
    else:
        _current.set(None)


def set_stdio_credential(
    token: str | None, *, requested_workspace_id: str | None = None
) -> None:
    """Bind (or clear) the stdio transport's credential for this process.

    Called by ``mcp_server.main()`` with the value of ``BRAND_OPS_MCP_TOKEN``.
    Setting it does NOT authenticate anything -- the credential is verified, and
    re-verified, on every tool call.
    """
    global _stdio_credential, _stdio_requested_workspace, _stdio_cache
    _stdio_credential = token or None
    _stdio_requested_workspace = requested_workspace_id or None
    _stdio_cache = None


def _snapshot(principal: _auth.Principal) -> ResolvedPrincipal | None:
    """Capture the scope ``_scope_principal`` just published, or None to refuse."""
    allowed = get_allowed_brands()
    if allowed is None:
        # An MCP call must never run with the "unscoped = every brand" sentinel.
        # Reaching here would mean the scoper did not publish a tenant scope.
        _log.error("MCP principal resolved without a brand scope; refusing")
        return None
    entitlements = get_current_entitlement()
    return ResolvedPrincipal(
        principal=principal,
        allowed_brands=frozenset(allowed),
        entitlements=tuple(entitlements) if entitlements is not None else None,
    )


async def resolve_mcp_principal(
    token: str | None, *, requested_workspace_id: str | None = None
) -> ResolvedPrincipal | None:
    """Verify a credential and resolve its tenant scope. None = refuse.

    Uses the human surface's own chain, in order:

      1. ``auth.authenticate`` -- JWKS-verified Keycloak (RS256/EdDSA, issuer and
         expiry enforced) or Brigade JWT, or a hashed, revocable Brand-Ops PAT;
      2. ``auth._scope_principal`` -- the membership-gated tenancy resolver, the
         PAT bindings, the entitlement resolution the generation gate reads, and
         the activity actor.

    Then it applies the one rule MCP adds on top of REST: a caller with no active
    workspace is REFUSED rather than served an empty view. The REST surface can
    usefully render "you have no workspace"; a tool call cannot, and serving it
    unscoped is precisely the hole this closes.
    """
    if not token:
        return None
    try:
        principal = await _auth.authenticate(token)
        if principal is None:
            _log.info("MCP credential rejected: not verifiable")
            return None
        scoped = await _auth._scope_principal(
            principal, requested_workspace_id, token
        )
        if not scoped.workspace_id:
            _log.info(
                "MCP credential rejected: %s has no active workspace membership",
                scoped.credential_kind or "caller",
            )
            return None
        return _snapshot(scoped)
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: any error denies
        _log.error("MCP credential resolution failed: %s", exc.__class__.__name__)
        return None


async def _stdio_principal() -> ResolvedPrincipal:
    """Resolve (or re-resolve) the stdio session's principal. Raises to refuse."""
    global _stdio_cache
    token = _stdio_credential
    if not token:
        raise MCPAuthError(
            "this MCP server refuses unauthenticated tool calls. Set "
            "BRAND_OPS_MCP_TOKEN to a Brand-Ops personal access token "
            "(brand_pat_...) or a suite bearer before starting it."
        )
    now = time.monotonic()
    cached = _stdio_cache
    if cached is not None and now < cached[0]:
        return cached[1]
    resolved = await resolve_mcp_principal(
        token, requested_workspace_id=_stdio_requested_workspace
    )
    if resolved is None:
        _stdio_cache = None
        raise MCPAuthError(
            "the configured MCP credential is invalid, expired, revoked, or has "
            "no active workspace membership; refusing the call."
        )
    _stdio_cache = (now + STDIO_REVALIDATE_SECONDS, resolved)
    return resolved


async def current_call_principal() -> ResolvedPrincipal:
    """The principal for THIS call, with its tenant scope installed. Raises to refuse.

    Order: the request-scoped principal the HTTP mount published, else the stdio
    session credential (re-verified on a short TTL). Either way the scope is
    re-published here, so a tool body can never execute under a scope that was
    left over from another call or never set at all.
    """
    resolved = _current.get()
    if resolved is None:
        resolved = await _stdio_principal()
    resolved.publish()
    return resolved


def install_mcp_guard(server) -> None:
    """Make every FastMCP dispatch require an authenticated, scoped principal.

    FastMCP registers its protocol handlers by capturing bound methods at
    construction time, so wrapping the instance methods and then re-running
    ``_setup_handlers()`` is what actually puts the guard in the served path --
    patching the tool functions themselves would miss resources and prompts, and
    patching the manager would miss nothing but be far more brittle.

    Guarded: ``call_tool`` (the action surface), ``read_resource`` and
    ``get_prompt`` (the data surfaces). NOT guarded: the ``list_*`` metadata
    handlers, which return exactly the tool/resource/prompt names the public
    ``.well-known/mcps.json`` manifest already publishes; gating them would add
    no confidentiality and would break capability negotiation for clients that
    enumerate before they authenticate. Idempotent: installing twice is a no-op.
    """
    if getattr(server, "_brand_ops_auth_guard", False):
        return

    original_call_tool = server.call_tool
    original_read_resource = server.read_resource
    original_get_prompt = server.get_prompt

    async def call_tool(name, arguments):
        await current_call_principal()
        return await original_call_tool(name, arguments)

    async def read_resource(uri):
        await current_call_principal()
        return await original_read_resource(uri)

    async def get_prompt(name, arguments=None):
        await current_call_principal()
        return await original_get_prompt(name, arguments)

    server.call_tool = call_tool
    server.read_resource = read_resource
    server.get_prompt = get_prompt
    # Re-register so the lowlevel server dispatches to the wrapped methods.
    server._setup_handlers()
    server._brand_ops_auth_guard = True
