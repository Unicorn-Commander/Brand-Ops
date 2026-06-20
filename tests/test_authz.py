"""In-workspace role authorization (the audit's "member is operationally an
owner" gap). RLS isolates tenants; require_role constrains what a member may DO
inside their tenant. The unscoped/anonymous (CLI / stdio MCP / tests / AUTH-off)
principal is exempt, preserving the original global behavior.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from brand_ops.api.auth import Principal, require_role


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
