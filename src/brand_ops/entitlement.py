"""Hosted-generation entitlement: may this workspace's tier run model gen?

One boolean gate, distinct from the dollar spend cap in cost.py. The cost cap
guards a budget within a paid tier (a soft, fail-open guard); this gate guards
who may spend at all, the on/off that separates a Free tier (on-device + card
paths only) from a paid tier (hosted, model-backed generation).

It is shipped DORMANT and is a complete no-op until an operator turns it on:

  * OFF by default (``BRAND_OPS_ENTITLEMENT_ENFORCED`` unset) -> :func:`enforce`
    returns immediately, so production behaves exactly as before. The suite dev
    wires the real tier source and flips this on once pricing is decided.
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
    on, so a half-built tier source cannot lock out today's users.

The tier signal is read from a verified token claim that ``api.auth`` publishes
into a request-scoped ContextVar, the same shape ``brand_loader`` uses for the
workspace id. This module is loop-free and DB-free: it reads only the flag, the
allow-list, and the in-request ContextVar (no network call). Resolving the claim
into the gate's input (the lookup, or minting the claim) is the suite's job;
Brand-Ops only reads what it is handed.
"""
from __future__ import annotations

import enum
import logging
from contextvars import ContextVar
from typing import Any

from .backends.base import EntitlementDenied
from .config import get_settings

_log = logging.getLogger("brand_ops.entitlement")

__all__ = [
    "Entitlement",
    "EntitlementDenied",
    "enforce",
    "get_current_entitlement",
    "interpret_claim",
    "reset_entitlement_scope",
    "set_entitlement_scope",
]


# ---------------------------------------------------------------------------
# Request-scoped entitlement signal
# ---------------------------------------------------------------------------
#
# ``api.auth._scope_principal`` reads the configured claim off the verified token
# and publishes its RAW value here, alongside the workspace scope it sets on
# ``brand_loader``. The sync engine then reads it at the chokepoint, exactly the
# way it reads the workspace id, with no engine signature change. A contextvar
# (not a module global) keeps the signal per-request and prevents bleed between
# concurrent async requests.
#
# The default is ``None`` = UNKNOWN/unscoped: no claim seen (the CLI, the stdio
# MCP, tests, AUTH off, or a token that simply does not carry the claim). The
# value can be any JSON-able claim shape the suite mints (a tier string, a bool,
# or a small dict); :func:`interpret_claim` is the one place that reads its
# meaning, so the gate does not care which shape is finalized.
_entitlement: ContextVar[Any | None] = ContextVar("brand_ops_entitlement", default=None)


def set_entitlement_scope(value: Any | None):
    """Publish this request's raw entitlement claim value. Returns the Token.

    ``api.auth`` calls this with the value of the configured claim key from the
    verified token (or ``None`` when the claim is absent). Passing ``None`` (or
    never calling this) leaves the signal UNKNOWN = the original unscoped
    behavior, where the gate (when enforced) fails closed.
    """
    return _entitlement.set(value)


def get_current_entitlement() -> Any | None:
    """This request's raw entitlement claim value, or None when unscoped/unknown."""
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
# Claim interpretation (the one place that reads the claim's meaning)
# ---------------------------------------------------------------------------


class Entitlement(enum.Enum):
    """The three outcomes of reading a claim value.

    ALLOW   -> the tier permits hosted generation.
    DENY    -> the tier explicitly excludes it (a Free tier).
    UNKNOWN -> no claim, or a value we cannot map. When enforced, UNKNOWN fails
               closed (deny), so a missing or unrecognized claim never becomes a
               free pass on the shared key.
    """

    ALLOW = "allow"
    DENY = "deny"
    UNKNOWN = "unknown"


# Tier strings (case-insensitive) that explicitly DENY hosted generation. Free
# is the only canonical one today; the suite's other canonical tier codes
# (founder_friend, vip_founder, byok, managed) are paid and read as ALLOW. The
# suite dev finalizes this split when pricing lands; until then DENY is the
# conservative, explicit set and everything else truthy reads as ALLOW.
_DENY_TIERS = frozenset({"free", "none", "unentitled"})


def interpret_claim(value: Any | None) -> Entitlement:
    """Interpret a raw claim value into ALLOW / DENY / UNKNOWN.

    The claim shape is not finalized suite-side, so this is deliberately liberal
    in what counts as "permits hosted generation", and conservative on what it
    treats as a definite NO:

      * ``None`` / missing               -> UNKNOWN (no signal; fails closed when
        enforced).
      * a bool                           -> ALLOW if True, DENY if False. A plain
        ``brand_ops_hosted: true|false`` claim works directly.
      * a string                         -> a tier/entitlement name. A name in
        ``_DENY_TIERS`` (e.g. "free") is DENY; any other non-empty string (e.g.
        "founder_friend", "paid", "pro", "vip_founder") is ALLOW; an empty/blank
        string is UNKNOWN.
      * a number                         -> ALLOW when non-zero, DENY when zero
        (a 0/1 entitlement flag minted as an int).
      * a dict                           -> look for a nested boolean
        ``hosted`` / ``hosted_generation`` / ``allowed`` / ``enabled`` (honored
        first), else a nested ``tier`` / ``entitlement`` / ``plan`` string read
        by the string rule above. An empty or unrecognized dict is UNKNOWN.
      * anything else                    -> UNKNOWN (never guess).

    Pure and total: it never raises and never touches the network or the DB.
    """
    if value is None:
        return Entitlement.UNKNOWN
    # bool is a subclass of int, so check it before the numeric branch.
    if isinstance(value, bool):
        return Entitlement.ALLOW if value else Entitlement.DENY
    if isinstance(value, str):
        return _interpret_tier_string(value)
    if isinstance(value, (int, float)):
        return Entitlement.ALLOW if value else Entitlement.DENY
    if isinstance(value, dict):
        return _interpret_claim_dict(value)
    return Entitlement.UNKNOWN


def _interpret_tier_string(value: str) -> Entitlement:
    """A tier/entitlement name -> verdict (see :func:`interpret_claim`)."""
    name = value.strip().lower()
    if not name:
        return Entitlement.UNKNOWN
    if name in _DENY_TIERS:
        return Entitlement.DENY
    return Entitlement.ALLOW


def _interpret_claim_dict(value: dict) -> Entitlement:
    """A structured claim -> verdict (boolean flag first, then a tier string)."""
    for flag_key in ("hosted", "hosted_generation", "allowed", "enabled"):
        flag = value.get(flag_key)
        if isinstance(flag, bool):
            return Entitlement.ALLOW if flag else Entitlement.DENY
    for tier_key in ("tier", "entitlement", "plan", "tier_code"):
        tier = value.get(tier_key)
        if isinstance(tier, str) and tier.strip():
            return _interpret_tier_string(tier)
    return Entitlement.UNKNOWN


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _allow_listed(workspace_id: str, allowlist: list[str]) -> bool:
    """Whether this workspace (or its org id, if the suite mints one as the claim)
    is on the bypass allow-list. A plain membership test, no network call.

    Brand-Ops has no org concept locally, so the allow-list is matched against
    the ``workspace_id`` in hand; an operator may also list org ids there for the
    day the entitlement claim carries one, since the same single set is checked.
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
      4. The entitlement claim resolves to ALLOW -> no-op.
      5. The claim resolves to DENY (a Free tier) or to UNKNOWN (no claim, an
         unmapped value) -> raise ``EntitlementDenied``. FAIL-CLOSED on UNKNOWN
         is deliberate and the opposite of the cost gate: this guards shared-key
         spend, so an unproven entitlement is never a free pass.

    Loop-free and DB-free: it reads only the flag, the allow-list, and the
    in-request ContextVar.
    """
    settings = get_settings()
    if not settings.entitlement_enforced:
        return
    if not workspace_id:
        return
    if _allow_listed(workspace_id, settings.entitlement_allowlist):
        return

    verdict = interpret_claim(get_current_entitlement())
    if verdict is Entitlement.ALLOW:
        return
    if verdict is Entitlement.UNKNOWN:
        # Fail closed, loudly: an enforced gate with no provable entitlement
        # denies, but says so, so an operator can tell a real Free denial apart
        # from a tier-source gap and fix the claim or the allow-list.
        _log.warning(
            "entitlement unknown for workspace=%s; denying hosted generation "
            "(fail-closed). Wire the entitlement claim or add the workspace to "
            "BRAND_OPS_ENTITLEMENT_ALLOWLIST.",
            workspace_id,
        )
    raise EntitlementDenied(
        "hosted generation is part of a paid plan. The on-device tools and the "
        "card composer stay available on this plan; upgrade to turn on "
        "model-backed brand generation."
    )
