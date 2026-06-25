"""Hosted-generation entitlement: may this workspace's tier run model gen?

One boolean gate, distinct from the dollar spend cap in cost.py. The cost cap
guards a budget within a paid tier (a soft, fail-open guard); this gate guards
who may spend at all, the on/off that separates a Free tier (on-device + card
paths only) from a paid tier (hosted, model-backed generation).

The tier signal is the suite's entitlement: a list of canonical feature_keys for
the caller's org, resolved by :mod:`entitlements_client` from the Ops-Center
endpoint and published into a request-scoped ContextVar by ``api.auth`` (the same
shape ``brand_loader`` uses for the workspace id). This module is the SYNC,
network-free half: it reads that already-resolved list at the generation
chokepoint and asks one question -- is Brand-Ops's ``feature_key``
(``brand_ops_access``) present? -- with no loop, no DB, and no network call. All
of the network, caching, and fail-soft/closed policy lives in the resolver; here
the published value is simply a list of grants, or ``None`` for UNKNOWN.

It is shipped DORMANT and is a complete no-op until an operator turns it on:

  * OFF by default (``BRAND_OPS_ENTITLEMENT_ENFORCED`` unset) -> :func:`enforce`
    returns immediately, so production behaves exactly as before. The suite dev
    flips this on once pricing is decided.
  * A no-op when unscoped (the CLI, the stdio MCP, tests, AUTH off): an unscoped
    run has no tenant to entitle, mirroring how the cost gate is a no-op without
    a workspace.
  * An allow-list bypass (``BRAND_OPS_ENTITLEMENT_ALLOWLIST``) so internal /
    dogfood / owner workspaces are never blocked the moment enforcement is first
    enabled.
  * FAIL-CLOSED on an unknown entitlement when enforced (deny what we cannot
    prove). This is the deliberate OPPOSITE of the cost gate's soft posture: the
    cost gate guards a budget (a store outage must not block a run), this gate
    guards shared-key spend (an unproven caller must not generate on our dime).
    It is only ever reached when an operator has explicitly turned enforcement
    on, so a half-built tier source cannot lock out today's users. (UNKNOWN is
    only ever published for a caller the resolver could not resolve AND has never
    seen; a known caller keeps their last good grant on a transient outage.)
"""
from __future__ import annotations

import enum
import logging
from contextvars import ContextVar

from .backends.base import EntitlementDenied
from .config import get_settings

_log = logging.getLogger("brand_ops.entitlement")

__all__ = [
    "Entitlement",
    "EntitlementDenied",
    "enforce",
    "get_current_entitlement",
    "interpret_entitlements",
    "reset_entitlement_scope",
    "set_entitlement_scope",
]


# ---------------------------------------------------------------------------
# Request-scoped entitlement signal
# ---------------------------------------------------------------------------
#
# ``api.auth._scope_principal`` resolves the caller's suite entitlements (via
# ``entitlements_client``) and publishes the resulting list of feature_keys here,
# alongside the workspace scope it sets on ``brand_loader``. The sync engine then
# reads it at the chokepoint, exactly the way it reads the workspace id, with no
# engine signature change. A contextvar (not a module global) keeps the signal
# per-request and prevents bleed between concurrent async requests.
#
# The default is ``None`` = UNKNOWN/unscoped: no entitlements resolved (the CLI,
# the stdio MCP, tests, AUTH off, the resolver dormant, or a Brigade/agent token
# we cannot forward to the uchub-issued endpoint). A list (possibly empty) means
# the suite answered: :func:`interpret_entitlements` reads its meaning.
_entitlement: ContextVar[list[str] | None] = ContextVar("brand_ops_entitlement", default=None)


def set_entitlement_scope(value: list[str] | None):
    """Publish this request's resolved entitlement feature_keys. Returns the Token.

    ``api.auth`` calls this with the list the resolver returned (or ``None`` when
    the resolver was dormant / could not resolve and has no cached grant). Passing
    ``None`` (or never calling this) leaves the signal UNKNOWN = the original
    unscoped behavior, where the gate (when enforced) fails closed.
    """
    return _entitlement.set(value)


def get_current_entitlement() -> list[str] | None:
    """This request's resolved feature_keys, or None when unscoped/unknown."""
    return _entitlement.get()


def reset_entitlement_scope(token=None) -> None:
    """Clear the request-scoped entitlement signal.

    With a Token (from :func:`set_entitlement_scope`) it restores the prior
    value; without one it hard-resets to None (UNKNOWN). Tests use the no-arg
    form so the signal never leaks between tests, mirroring
    ``brand_loader.reset_workspace_scope``.
    """
    if token is not None:
        _entitlement.reset(token)
    else:
        _entitlement.set(None)


# ---------------------------------------------------------------------------
# Entitlement interpretation (the one place that reads the grant's meaning)
# ---------------------------------------------------------------------------


class Entitlement(enum.Enum):
    """The three outcomes of reading a resolved entitlement.

    ALLOW   -> the org's grants include hosted generation (the feature_key).
    DENY    -> the suite answered, and the feature_key is not in the grant list
               (a Free / unentitled org).
    UNKNOWN -> nothing resolved (no list). When enforced, UNKNOWN fails closed
               (deny), so an unresolved entitlement never becomes a free pass on
               the shared key.
    """

    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"


def interpret_entitlements(value: list[str] | None, feature_key: str) -> Entitlement:
    """Interpret a resolved entitlement list against the required feature_key.

      * ``None``                 -> UNKNOWN (nothing resolved; fails closed when
        enforced).
      * a list CONTAINING the    -> ALLOW.
        feature_key
      * a list WITHOUT it        -> DENY (the suite answered; the grant is absent).
        (including an empty list)
      * anything else            -> UNKNOWN (never guess).

    Pure and total: it never raises and never touches the network or the DB.
    """
    if value is None:
        return Entitlement.UNKNOWN
    if isinstance(value, list):
        return Entitlement.ALLOW if feature_key in value else Entitlement.DENY
    return Entitlement.UNKNOWN


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _allow_listed(workspace_id: str, allowlist: list[str]) -> bool:
    """Whether this workspace (or its org id, if the suite mints one as the claim)
    is on the bypass allow-list. A plain membership test, no network call.

    Brand-Ops has no org concept locally, so the allow-list is matched against
    the ``workspace_id`` in hand; an operator may also list org ids there for the
    day the resolver keys on one, since the same single set is checked.
    """
    return workspace_id in allowlist


def enforce(workspace_id: str | None) -> None:
    """Deny hosted generation a workspace's tier does not include. Raises
    ``EntitlementDenied`` (a ``BackendError`` the handlers map to 402).

    The decision order (see the module docstring for the why of each):

      1. Enforcement off (``BRAND_OPS_ENTITLEMENT_ENFORCED`` unset, the default)
         -> no-op. This is the production default: a complete no-op so behavior
         is byte-identical until an operator flips it on.
      2. No active workspace (the unscoped CLI / stdio MCP / tests / AUTH off)
         -> no-op. An unscoped run has no tenant to entitle, mirroring the cost
         gate.
      3. Workspace on the allow-list -> no-op. Internal / dogfood / owner
         workspaces are never blocked.
      4. The resolved grants include the feature_key (ALLOW) -> no-op.
      5. The suite answered without the feature_key (DENY, a Free tier) or nothing
         resolved (UNKNOWN) -> raise ``EntitlementDenied``. FAIL-CLOSED on UNKNOWN
         is deliberate and the opposite of the cost gate: this guards shared-key
         spend, so an unproven entitlement is never a free pass.

    Loop-free and DB-free: it reads only the flag, the allow-list, the feature_key,
    and the in-request ContextVar.
    """
    settings = get_settings()
    if not settings.entitlement_enforced:
        return
    if not workspace_id:
        return
    if _allow_listed(workspace_id, settings.entitlement_allowlist):
        return

    verdict = interpret_entitlements(get_current_entitlement(), settings.entitlement_feature_key)
    if verdict is Entitlement.ALLOW:
        return
    if verdict is Entitlement.UNKNOWN:
        # Fail closed, loudly: an enforced gate with no resolved entitlement
        # denies, but says so, so an operator can tell a real Free denial apart
        # from a resolver gap and fix the endpoint config or the allow-list.
        _log.warning(
            "entitlement unknown for workspace=%s; denying hosted generation "
            "(fail-closed). Check BRAND_OPS_ENTITLEMENT_URL / the entitlements "
            "endpoint, or add the workspace to BRAND_OPS_ENTITLEMENT_ALLOWLIST.",
            workspace_id,
        )
    raise EntitlementDenied(
        "hosted generation is part of a paid plan. The on-device tools and the "
        "card composer stay available on this plan; upgrade to turn on "
        "model-backed brand generation."
    )
