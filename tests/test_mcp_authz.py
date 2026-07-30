"""The AUTHORIZATION axis of the MCP lane: role, not just identity.

The previous change made every MCP surface authenticate and tenant-scope its
caller. It did not make the MCP lane consult the in-workspace ROLE gate the REST
lane enforces, and that was a real, reproduced privilege escalation:

    a viewer-role member of a workspace got
        403 "this action requires the owner role in this workspace"
    from POST /api/brands/<slug>/clone, and
        200, plus a brand actually written to disk,
    from POST /mcp -> tools/call clone_brand.

Same for approve_asset, transition_asset, intake_brand, create_agent,
generate_agent_avatar, generate_agent_variants, update_agent and set_agent_voice.

What is pinned here:

  * the two lanes AGREE, tool by tool, for the same caller;
  * the gate is a single chokepoint with a declarative table, and a tool that is
    not in the table is REFUSED (so a tool added later cannot ship ungated);
  * the gate refuses without also refusing everything -- an owner still gets
    through and the tool really runs;
  * the two mutations that survived the previous round: the fail-CLOSED
    ``except`` in ``resolve_mcp_principal`` and the "refuse the all-tenant
    sentinel" guard in ``_snapshot``;
  * the HTTP transport's DNS-rebinding allow-list, which was pinned to loopback
    and therefore answered 421 to every request under a real hostname.

No network, no database.
"""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# app before mcp_server: app -> mcp_http -> mcp_server, which imports back into
# brand_ops.api (same note as tests/test_mcp.py and tests/test_mcp_auth.py).
from brand_ops.api.app import create_app  # isort: skip
from brand_ops import brand_loader
from brand_ops.api import auth as auth_mod
from brand_ops.api import mcp_auth
from brand_ops.api.auth import Principal, require_principal
from brand_ops.mcp_server import mcp

WS_A = "019e50a3-d995-723f-ab66-0f765f92c0f4"

# Every owner-gated MCP tool, paired with the REST route that does the same
# thing and the role that route demands today (``_Owner`` in api/routes.py).
# This is the mapping the fix mirrors; the list is written out here so that
# changing one lane without the other fails a test.
OWNER_TOOLS = (
    "approve_asset",
    "clone_brand",
    "create_agent",
    "generate_agent_avatar",
    "generate_agent_variants",
    "intake_brand",
    "new_brand",
    "set_agent_voice",
    "transition_asset",
    "update_agent",
)


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    brand_loader.reset_workspace_scope()
    mcp_auth.reset_mcp_principal()
    mcp_auth.set_stdio_credential(None)
    try:
        yield
    finally:
        brand_loader.reset_workspace_scope()
        mcp_auth.reset_mcp_principal()
        mcp_auth.set_stdio_credential(None)


def _resolved(role: str, brands: set[str] | None = None) -> mcp_auth.ResolvedPrincipal:
    """A principal that has already authenticated, with the given workspace role.

    Built directly rather than through a signed token because these tests are
    about the SECOND axis: authentication is covered end to end in
    tests/test_mcp_auth.py, and re-minting an RSA token here would only make the
    role assertion harder to read.
    """
    return mcp_auth.ResolvedPrincipal(
        principal=Principal(
            uc_uid="user-a",
            issuer="keycloak",
            credential_kind="keycloak",
            workspace_id=WS_A,
            workspace_slug="ws-a",
            role=role,
        ),
        allowed_brands=frozenset(brands if brands is not None else {"magic-unicorn"}),
        entitlements=None,
    )


@pytest.fixture
def kits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One workspace with one real brand on disk, so a write tool can be observed."""
    root = tmp_path / "brand-kits"
    (root / WS_A).mkdir(parents=True)
    shutil.copytree(
        brand_loader.DEFAULT_KITS_DIR / "magic-unicorn", root / WS_A / "magic-unicorn"
    )
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)
    brand_loader.clear_brand_cache()
    return root


# ---------------------------------------------------------------------------
# The table itself: complete, mirrored, and deny-by-default
# ---------------------------------------------------------------------------


def test_every_registered_tool_has_a_declared_role() -> None:
    """No tool may be reachable without a declared minimum role.

    This is the "a future tool cannot silently ship ungated" assertion: adding a
    @mcp.tool without adding a row to TOOL_MIN_ROLE fails HERE, at the moment it
    is written, rather than shipping as an ungated capability. (It would also be
    refused at runtime -- see test_an_undeclared_tool_is_denied -- so the failure
    mode is closed either way; this just makes it loud.)
    """
    registered = {t.name for t in asyncio.run(mcp.list_tools())}
    assert registered, "the server must expose tools at all"
    undeclared = sorted(registered - set(mcp_auth.TOOL_MIN_ROLE))
    assert undeclared == [], f"MCP tools with no declared minimum role: {undeclared}"
    stale = sorted(set(mcp_auth.TOOL_MIN_ROLE) - registered)
    assert stale == [], f"TOOL_MIN_ROLE names tools that do not exist: {stale}"


def test_the_owner_gated_set_is_exactly_the_rest_owner_set() -> None:
    """The owner column of the table is the mirror of the ``_Owner`` REST routes."""
    declared = sorted(
        name for name, role in mcp_auth.TOOL_MIN_ROLE.items() if role == "owner"
    )
    assert declared == sorted(OWNER_TOOLS)


def test_every_declared_role_is_a_real_role() -> None:
    """A typo'd role must not silently become rank 0 (i.e. no gate at all)."""
    for name, role in mcp_auth.TOOL_MIN_ROLE.items():
        assert role in auth_mod._ROLE_RANK, f"{name} declares unknown role {role!r}"
    assert mcp_auth.RESOURCE_MIN_ROLE in auth_mod._ROLE_RANK
    assert mcp_auth.PROMPT_MIN_ROLE in auth_mod._ROLE_RANK


def test_an_undeclared_tool_is_denied() -> None:
    """Deny by default: an unmapped name is refused even for an owner."""
    with pytest.raises(mcp_auth.MCPAuthzError):
        mcp_auth.authorize_tool("some_tool_added_next_quarter", _resolved("owner"))


def test_mcp_and_rest_rank_roles_identically() -> None:
    """One hierarchy, one unknown-role-ranks-zero rule, both lanes."""
    assert mcp_auth._role_rank("owner") == auth_mod._ROLE_RANK["owner"]
    assert mcp_auth._role_rank("OWNER") == auth_mod._ROLE_RANK["owner"]
    assert mcp_auth._role_rank("wizard") == 0
    assert mcp_auth._role_rank(None) == 0


# ---------------------------------------------------------------------------
# The escalation itself, through the real dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", OWNER_TOOLS)
def test_a_viewer_is_refused_every_owner_tool_through_the_dispatch(tool: str) -> None:
    """The headline. Each of these returned 200 and did the work for a viewer."""
    mcp_auth.set_mcp_principal(_resolved("viewer"))
    with pytest.raises(mcp_auth.MCPAuthzError) as exc:
        asyncio.run(mcp.call_tool(tool, {}))
    assert "owner role in this workspace" in str(exc.value)


@pytest.mark.parametrize("tool", OWNER_TOOLS)
def test_member_and_admin_are_refused_owner_tools_too(tool: str) -> None:
    """The gate is the owner rank, not merely "not a viewer"."""
    for role in ("member", "admin"):
        mcp_auth.set_mcp_principal(_resolved(role))
        with pytest.raises(mcp_auth.MCPAuthzError):
            asyncio.run(mcp.call_tool(tool, {}))


def test_an_unrecognised_role_is_refused_an_owner_tool() -> None:
    """Indeterminate denies: a legacy/malformed role ranks 0, never owner."""
    mcp_auth.set_mcp_principal(_resolved("superuser"))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(mcp.call_tool("clone_brand", {}))
    mcp_auth.set_mcp_principal(_resolved(""))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(mcp.call_tool("clone_brand", {}))


def test_the_refusal_happens_before_the_tool_body_runs(
    monkeypatch: pytest.MonkeyPatch, kits: Path
) -> None:
    """A refused write must not touch the disk -- the escalation wrote a brand."""
    import brand_ops.mcp_server as server

    ran: list[str] = []
    monkeypatch.setattr(
        server, "_clone_brand", lambda *a, **k: ran.append("cloned")  # pragma: no cover
    )
    mcp_auth.set_mcp_principal(_resolved("viewer"))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(
            mcp.call_tool(
                "clone_brand",
                {
                    "slug": "magic-unicorn",
                    "new_slug": "sneaky-clone",
                    "display_name": "Sneaky Clone",
                },
            )
        )
    assert ran == []
    assert not (kits / WS_A / "sneaky-clone").exists()


def test_an_owner_still_gets_through_and_the_tool_really_runs(kits: Path) -> None:
    """The gate must refuse the right callers, not all of them.

    Without this, deleting the whole table (or gating everything at owner) would
    look like a pass.
    """
    mcp_auth.set_mcp_principal(_resolved("owner"))
    result = repr(
        asyncio.run(
            mcp.call_tool(
                "clone_brand",
                {
                    "slug": "magic-unicorn",
                    "new_slug": "legit-clone",
                    "display_name": "Legit Clone",
                },
            )
        )
    )
    assert "error" not in result.lower(), result
    assert (kits / WS_A / "legit-clone" / "brand.yaml").is_file()


def test_a_viewer_keeps_the_reads_the_rest_surface_gives_them(kits: Path) -> None:
    """The mirror is not "deny viewers everything": the _Auth routes stay open.

    ``GET /api/brands`` is ``_Auth`` (any authenticated member), so ``list_brands``
    must answer a viewer -- otherwise this change would be a regression dressed up
    as a fix.
    """
    mcp_auth.set_mcp_principal(_resolved("viewer"))
    listed = repr(asyncio.run(mcp.call_tool("list_brands", {})))
    assert "magic-unicorn" in listed


def test_resources_and_prompts_are_role_checked_and_open_to_a_viewer() -> None:
    """The read surfaces mirror the ``_Auth`` REST reads: viewer passes."""
    mcp_auth.set_mcp_principal(_resolved("viewer"))
    assert asyncio.run(mcp.read_resource("brand-ops://asset-types"))
    assert asyncio.run(mcp.get_prompt("audit_brand_compliance", {"brand": "x"}))


def test_resource_and_prompt_reads_go_through_the_authorizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not just "they happen to pass": they are WIRED to the same chokepoint.

    ``viewer`` is the floor rank, so a viewer-minimum gate can never refuse
    anybody -- passing proves nothing on its own. Raising the declared minimum
    proves the dispatch really consults it, which is what has to stay true if
    these surfaces are ever tightened.
    """
    monkeypatch.setattr(mcp_auth, "RESOURCE_MIN_ROLE", "owner")
    monkeypatch.setattr(mcp_auth, "PROMPT_MIN_ROLE", "owner")
    mcp_auth.set_mcp_principal(_resolved("viewer"))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(mcp.read_resource("brand-ops://asset-types"))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(mcp.get_prompt("audit_brand_compliance", {"brand": "x"}))


# ---------------------------------------------------------------------------
# The two lanes, side by side, for the same caller
# ---------------------------------------------------------------------------


def _viewer_rest_client() -> TestClient:
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid="user-a",
        issuer="keycloak",
        credential_kind="keycloak",
        workspace_id=WS_A,
        workspace_slug="ws-a",
        role="viewer",
    )
    return TestClient(app)


def test_rest_and_mcp_agree_that_a_viewer_cannot_clone(kits: Path) -> None:
    """One caller, two doors, one answer. This pair is the reproduced escalation."""
    body = {"new_slug": "viewer-clone", "display_name": "Viewer Clone"}
    with _viewer_rest_client() as client:
        rest = client.post("/api/brands/magic-unicorn/clone", json=body)
    assert rest.status_code == 403
    assert "owner role in this workspace" in rest.text

    mcp_auth.set_mcp_principal(_resolved("viewer"))
    with pytest.raises(mcp_auth.MCPAuthzError) as exc:
        asyncio.run(
            mcp.call_tool(
                "clone_brand",
                {"slug": "magic-unicorn", **body},
            )
        )
    assert "owner role in this workspace" in str(exc.value)
    assert not (kits / WS_A / "viewer-clone").exists()


def test_rest_and_mcp_agree_that_a_viewer_cannot_approve_an_asset() -> None:
    """The second half of the report: the asset lifecycle moves."""
    with _viewer_rest_client() as client:
        rest = client.post(
            "/api/assets/approve",
            json={"brand": "magic-unicorn", "asset": "og_image", "file": "a.png"},
        )
    assert rest.status_code == 403

    mcp_auth.set_mcp_principal(_resolved("viewer"))
    with pytest.raises(mcp_auth.MCPAuthzError):
        asyncio.run(mcp.call_tool("approve_asset", {}))


# ---------------------------------------------------------------------------
# The two surviving mutations
# ---------------------------------------------------------------------------


def test_resolve_mcp_principal_fails_closed_when_the_chain_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION PIN: ``except Exception: return None`` in resolve_mcp_principal.

    Replacing that handler with a fail-OPEN (returning an unscoped principal on
    error) left all 39 tests green. It must not: a JWKS outage, a database fault,
    or any unexpected bug in the chain has to DENY. The assertion is on the
    RETURN VALUE, so returning any principal at all -- scoped, unscoped, empty --
    is red, and the dispatch is checked too so a fail-open cannot slip in one
    layer up.
    """

    async def _boom(token):
        raise RuntimeError("JWKS host is down")

    monkeypatch.setattr(auth_mod, "authenticate", _boom)

    assert asyncio.run(mcp_auth.resolve_mcp_principal("any-token")) is None

    # And the same fault on a live stdio session refuses the call rather than
    # letting it run under whatever scope happened to be installed.
    brand_loader.set_workspace_scope(None, None)
    mcp_auth.set_stdio_credential("brand_pat_" + "a" * 40)
    with pytest.raises(mcp_auth.MCPAuthError):
        asyncio.run(mcp.call_tool("list_brands", {}))


def test_resolve_mcp_principal_fails_closed_when_the_scoper_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same, one step later: a fault in the tenancy resolver also denies."""

    async def _ok(token):
        return Principal(uc_uid="user-a", issuer="keycloak", credential_kind="keycloak")

    async def _boom(principal, requested, raw=None):
        raise RuntimeError("tenancy database is unreachable")

    monkeypatch.setattr(auth_mod, "authenticate", _ok)
    monkeypatch.setattr(auth_mod, "_scope_principal", _boom)
    assert asyncio.run(mcp_auth.resolve_mcp_principal("any-token")) is None


def test_snapshot_refuses_the_all_tenant_sentinel() -> None:
    """MUTATION PIN: the ``get_allowed_brands() is None`` guard in ``_snapshot``.

    ``None`` is the loader's "every brand, in every tenant" sentinel -- the scope
    the anonymous/CLI path runs under. An MCP call must never run under it, and
    this one line is what stops it. Deleting the line left 39/39 green.

    The assertion is deliberately on ``_snapshot`` directly: it must RETURN None,
    so both plausible mutations are red -- deleting the check outright (which
    raises TypeError on ``frozenset(None)``) and "helpfully" softening it to
    ``frozenset(allowed or ())`` (which returns a principal).
    """
    brand_loader.set_workspace_scope(WS_A, None)
    assert brand_loader.get_allowed_brands() is None, "precondition: the sentinel"
    principal = Principal(
        uc_uid="user-a",
        issuer="keycloak",
        credential_kind="keycloak",
        workspace_id=WS_A,
        role="owner",
    )
    assert mcp_auth._snapshot(principal) is None


def test_a_scoper_that_publishes_no_scope_cannot_produce_a_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: the sentinel path refuses, it does not serve every tenant."""

    async def _ok(token):
        return Principal(uc_uid="user-a", issuer="keycloak", credential_kind="keycloak")

    async def _scope_but_leave_it_global(principal, requested, raw=None):
        # A workspace resolves, but the brand allow-list is left unset -- exactly
        # the "scoper did not publish a tenant scope" condition.
        brand_loader.set_workspace_scope(WS_A, None)
        from dataclasses import replace

        return replace(principal, workspace_id=WS_A, role="owner")

    monkeypatch.setattr(auth_mod, "authenticate", _ok)
    monkeypatch.setattr(auth_mod, "_scope_principal", _scope_but_leave_it_global)
    assert asyncio.run(mcp_auth.resolve_mcp_principal("any-token")) is None


def test_a_resolved_principal_can_never_carry_the_sentinel() -> None:
    """The type itself: allowed_brands is a frozenset, so None cannot be smuggled.

    ``publish()`` writes it straight into ``set_workspace_scope``; an empty
    frozenset means "no brands" while None would mean "all brands, all tenants",
    and those two must never be confusable at this boundary.
    """
    resolved = _resolved("owner", brands=set())
    resolved.publish()
    assert brand_loader.get_allowed_brands() == frozenset()
    assert brand_loader.get_allowed_brands() is not None
