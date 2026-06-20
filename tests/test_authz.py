"""In-workspace role authorization (the audit's "member is operationally an
owner" gap). RLS isolates tenants; require_role constrains what a member may DO
inside their tenant. The unscoped/anonymous (CLI / stdio MCP / tests / AUTH-off)
principal is exempt, preserving the original global behavior.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from brand_ops.api.auth import Principal, agent_scopes_satisfied, require_role


def _run(dep, principal):
    return asyncio.run(dep(principal))


def test_owner_passes_owner_gate():
    dep = require_role("owner")
    p = Principal(uc_uid="u", role="owner", workspace_id="w")
    assert _run(dep, p) is p


@pytest.mark.parametrize("role", ["member", "viewer", "weird", "", None])
def test_non_owner_denied_owner_gate(role):
    dep = require_role("owner")
    with pytest.raises(HTTPException) as exc:
        _run(dep, Principal(uc_uid="u", role=role, workspace_id="w"))
    assert exc.value.status_code == 403


def test_anonymous_is_exempt():
    # AUTH off / CLI / stdio MCP / tests: unscoped trusted context, no role gate.
    dep = require_role("owner")
    p = Principal(uc_uid="anonymous", anonymous=True)
    assert _run(dep, p) is p


def test_role_hierarchy_member_gate():
    dep = require_role("member")
    assert _run(dep, Principal(uc_uid="u", role="owner")) is not None
    assert _run(dep, Principal(uc_uid="u", role="member")) is not None
    with pytest.raises(HTTPException):
        _run(dep, Principal(uc_uid="u", role="viewer"))


def test_admin_outranks_member_below_owner():
    assert _run(require_role("member"), Principal(uc_uid="u", role="admin")) is not None
    with pytest.raises(HTTPException):
        _run(require_role("owner"), Principal(uc_uid="u", role="admin"))


# ─────────────────── delegated agent-token scope enforcement (#3) ────────────


def _agent(scopes=()):
    return Principal(uc_uid="bot", agent_id="agent-x", scopes=tuple(scopes))


def test_scopes_noop_when_disabled():
    # Default-off (no Brigade scope-minting contract yet): even a scope-less agent
    # token passes, so flipping the flag on later is the only behavior change.
    assert agent_scopes_satisfied(_agent(), ("brandops:generate",), enforce=False) is True


def test_human_and_anonymous_exempt_from_scope_gate():
    human = Principal(uc_uid="u", role="owner")  # agent_id None -> a human SSO caller
    assert agent_scopes_satisfied(human, ("brandops:generate",), enforce=True) is True
    anon = Principal(uc_uid="anonymous", anonymous=True)
    assert agent_scopes_satisfied(anon, ("brandops:generate",), enforce=True) is True


def test_agent_without_required_scope_denied_when_enforced():
    assert agent_scopes_satisfied(_agent(), ("brandops:generate",), enforce=True) is False
    # holding one of two required scopes is still a denial (needs ALL)
    assert (
        agent_scopes_satisfied(
            _agent(["brandops:generate"]),
            ("brandops:generate", "brandops:research"),
            enforce=True,
        )
        is False
    )


def test_agent_with_required_scope_allowed_when_enforced():
    assert (
        agent_scopes_satisfied(
            _agent(["brandops:generate"]), ("brandops:generate",), enforce=True
        )
        is True
    )
