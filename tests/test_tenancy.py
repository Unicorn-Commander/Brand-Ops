"""Workspace-scoped multi-tenancy tests for the file brand-loader boundary.

Brand scoping is centralized in ``brand_loader`` via a per-request scope -- a
(workspace_id, allowed_brands) pair -- that ``require_principal`` resolves from
the caller's memberships and publishes for the request. ``list_brands`` /
``load_brand`` then reveal only the brands in the allow-list. This file covers
the scope mechanism itself, without a database:

* UNSCOPED (allowed_brands None: the CLI / stdio MCP / local dev / tests, and the
  anonymous principal when AUTH_REQUIRED is off) sees every brand -- backward
  compatible, nothing regresses.
* SCOPED to a set sees only those brands; a brand outside the set reads as a 404
  (``BrandNotFound``), never a 403 leak.
* SCOPED to the EMPTY set (an authed caller with no usable membership) sees
  nothing -- deny-by-default.
* a brand created while scoped is stamped with the active workspace and made
  visible for the rest of that request.

The full DB / RLS / resolver path (membership enumeration, real Postgres
isolation) is covered separately in test_db_tenancy.py, which needs a Postgres.

The scope is reset in an autouse fixture so it never leaks between tests.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brand_ops import brand_loader
from brand_ops.api.app import create_app
from brand_ops.brand_clone import create_brand_from_draft
from brand_ops.brand_loader import (
    DEFAULT_KITS_DIR,
    BrandNotFound,
    allow_brand,
    list_brands,
    load_brand,
    set_workspace_scope,
    workspace_query_suffix,
)

# A stable subset of the seed brands shipped in brand-kits/. Asserted with <= so
# adding seed brands never breaks these tests.
SEED_BRANDS = {
    "center-deep",
    "magic-unicorn",
    "majiks",
    "majiks-studio",
    "unicorn-commander",
}
# A canonical-looking workspace uuid for the scoped cases. Its value is opaque
# here: the file loader gates on the brand-slug set, not the id.
WS = "019e50a3-d995-723f-ab66-0f765f92c0f4"


@pytest.fixture(autouse=True)
def _reset_scope() -> Iterator[None]:
    """Clear the request-scoped workspace scope before and after every test."""
    brand_loader.reset_workspace_scope()
    try:
        yield
    finally:
        brand_loader.reset_workspace_scope()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off (local/dev posture): every request resolves to the anonymous
    # principal, which is UNSCOPED. No token, no Keycloak, no DB, no network.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    """A throwaway copy of the repo brand-kits, so writes never touch the repo."""
    dest = tmp_path / "brand-kits"
    shutil.copytree(DEFAULT_KITS_DIR, dest)
    return dest


@pytest.fixture
def scoped_kits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A per-workspace kits tree: ``brand-kits/<WS>/<slug>/`` for a few seed brands.

    A scoped read resolves through ``current_kits_dir()`` to ``<root>/<WS>``, where
    these brands physically live, so this exercises the real per-workspace layout
    (the on-disk isolation) on top of the allow-list filter. Heavy generated dirs
    are skipped so the copy is just the authoring files.
    """
    root = tmp_path / "brand-kits"
    ws_dir = root / WS
    ws_dir.mkdir(parents=True)
    skip = shutil.ignore_patterns("generated", "imported", "refs", "agents")
    for slug in ("center-deep", "majiks", "magic-unicorn"):
        shutil.copytree(DEFAULT_KITS_DIR / slug, ws_dir / slug, ignore=skip)
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)
    return root


# ─────────────────── unscoped (anonymous / CLI) sees all ────────────────────


def test_anonymous_unscoped_sees_all_seed_brands(client: TestClient) -> None:
    # AUTH_REQUIRED off -> anonymous principal -> unscoped -> every seed brand.
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["brands"]}
    assert SEED_BRANDS <= slugs, f"missing seed brands: {SEED_BRANDS - slugs}"


def test_anonymous_brand_detail_is_200(client: TestClient) -> None:
    resp = client.get("/api/brands/center-deep")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "center-deep"


@pytest.mark.asyncio
async def test_require_principal_anonymous_is_unscoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no credentials and auth off, the dependency returns the anonymous
    # principal with NO workspace and publishes the UNSCOPED scope (allowed None).
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    from brand_ops.api.auth import require_principal

    principal = await require_principal(credentials=None, x_workspace_id=None)
    assert principal.anonymous is True
    assert principal.workspace_id is None
    assert brand_loader.get_allowed_brands() is None


def test_unscoped_lists_every_brand() -> None:
    assert brand_loader.get_allowed_brands() is None
    assert SEED_BRANDS <= set(list_brands())


# ──────────────── scoped to a set: only those brands ────────────────────────


def test_scoped_to_subset_lists_only_those(scoped_kits: Path) -> None:
    set_workspace_scope(WS, frozenset({"center-deep", "majiks"}))
    assert set(list_brands()) == {"center-deep", "majiks"}


def test_scoped_cross_tenant_read_is_brandnotfound() -> None:
    # magic-unicorn is not in this workspace's allow-list -> 404, never a 403.
    set_workspace_scope(WS, frozenset({"center-deep"}))
    with pytest.raises(BrandNotFound):
        load_brand("magic-unicorn")


def test_scoped_in_set_still_resolves(scoped_kits: Path) -> None:
    set_workspace_scope(WS, frozenset({"magic-unicorn"}))
    assert load_brand("magic-unicorn").brand.slug == "magic-unicorn"


# ──────────────── deny-by-default: empty allow-list sees nothing ────────────


def test_empty_allow_list_lists_nothing() -> None:
    # An authed caller with no usable membership is scoped to the empty set.
    set_workspace_scope(None, frozenset())
    assert list_brands() == []


def test_empty_allow_list_read_is_brandnotfound() -> None:
    set_workspace_scope(None, frozenset())
    with pytest.raises(BrandNotFound):
        load_brand("magic-unicorn")


# ──────────────── allow_brand: a just-created brand becomes visible ─────────


def test_allow_brand_adds_to_scope(scoped_kits: Path) -> None:
    set_workspace_scope(WS, frozenset({"center-deep"}))
    with pytest.raises(BrandNotFound):
        load_brand("magic-unicorn")
    allow_brand("magic-unicorn")
    assert load_brand("magic-unicorn").brand.slug == "magic-unicorn"


def test_allow_brand_noop_when_unscoped() -> None:
    # Unscoped: allow_brand is a no-op (everything is already visible).
    allow_brand("whatever")
    assert brand_loader.get_allowed_brands() is None


# ──────────── a brand created while scoped records its workspace ────────────


_DRAFT = {
    "palette": {"primary": "#123456", "accent": "#abcdef"},
    "voice_tone": ["bold"],
    "style_keywords": ["clean"],
    "display_name": "Acme Brand",
}


def test_created_brand_records_active_workspace(kits: Path) -> None:
    # Created while scoped to WS (with an empty starting allow-list): the written
    # brand.yaml records workspace_id=WS, and the brand is visible for the rest
    # of this request because the create path called allow_brand.
    set_workspace_scope(WS, frozenset())
    brand = create_brand_from_draft("acme-brand", _DRAFT, kits_dir=kits)
    assert brand.brand.workspace_id == WS
    assert "acme-brand" in list_brands(kits_dir=kits)


def test_created_brand_unscoped_has_no_workspace(kits: Path) -> None:
    # Unscoped create (CLI): no active workspace, so workspace_id is None and the
    # boot importer will later assign it to the deploy's bootstrap workspace.
    brand = create_brand_from_draft("acme-brand", _DRAFT, kits_dir=kits)
    assert brand.brand.workspace_id is None


def test_mcp_list_generated_assets_gated() -> None:
    # The MCP catalog tool must honor the workspace allow-list: a brand outside
    # the workspace discloses nothing (the scoped-MCP catalog leak the review
    # found). Unscoped it reads all, like the rest of the stdio/CLI surface.
    from brand_ops.mcp_server import list_generated_assets

    set_workspace_scope(WS, frozenset({"center-deep"}))
    assert list_generated_assets(brand="magic-unicorn") == {"assets": [], "count": 0}


# ─────── prod hardening 1: ?ws= image scoping (URL suffix + auth fallback) ────
# An <img> GET cannot send the X-Workspace-Id header the fetch() calls use, so a
# multi-workspace caller's image would resolve to their DEFAULT workspace and
# 404 on a brand they are viewing in another. Asset URLs carry &ws=<active>, and
# require_principal honors ?ws= as a membership-gated fallback. A single-tenant
# deploy never hits this; a public multi-tenant one does.


def test_workspace_query_suffix_unscoped_is_empty() -> None:
    assert workspace_query_suffix() == ""


def test_workspace_query_suffix_scoped_carries_ws() -> None:
    set_workspace_scope(WS, frozenset({"center-deep"}))
    assert workspace_query_suffix() == f"&ws={WS}"


def test_catalog_url_carries_ws_when_scoped() -> None:
    # The Library's asset URLs must self-identify their workspace.
    from brand_ops.api.catalog import _coerce_record

    set_workspace_scope(WS, frozenset({"center-deep"}))
    rec = _coerce_record(
        {"asset_type": "logo", "brand": "center-deep"},
        Path("logo.json"),
        Path("logo.png"),
    )
    assert rec is not None and rec.url is not None
    assert rec.url.endswith(f"&ws={WS}")


def test_catalog_url_has_no_ws_when_unscoped() -> None:
    from brand_ops.api.catalog import _coerce_record

    rec = _coerce_record(
        {"asset_type": "logo", "brand": "center-deep"},
        Path("logo.json"),
        Path("logo.png"),
    )
    assert rec is not None and rec.url is not None
    assert "ws=" not in rec.url


@pytest.mark.asyncio
async def test_require_principal_honors_ws_query_when_header_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ?ws= query must feed the SAME membership-gated resolution the header
    # does. The resolver is stubbed to echo what it was asked for, so we can
    # assert the query value reached it -- and that the header still wins.
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    from fastapi.security import HTTPAuthorizationCredentials

    from brand_ops.api import auth as auth_mod
    from brand_ops.db.tenancy import WorkspaceContext

    captured: dict[str, str | None] = {}

    async def fake_authenticate(token: str) -> auth_mod.Principal:
        return auth_mod.Principal(uc_uid="u1", issuer="keycloak")

    async def fake_resolve(uc_uid: str, requested_workspace_id: str | None = None):
        captured["requested"] = requested_workspace_id
        return WorkspaceContext(
            workspace_id=requested_workspace_id,
            workspace_slug="w",
            role="member",
            allowed_brand_slugs=frozenset({"center-deep"}),
        )

    monkeypatch.setattr(auth_mod, "authenticate", fake_authenticate)
    monkeypatch.setattr(auth_mod, "resolve_context", fake_resolve)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")

    # Header absent -> the ?ws= query is used as the requested workspace.
    p = await auth_mod.require_principal(credentials=creds, x_workspace_id=None, ws=WS)
    assert captured["requested"] == WS
    assert p.workspace_id == WS

    # Header present -> it wins over the query (D4 precedence preserved).
    other = "019e5111-0000-7000-a000-000000000000"
    p2 = await auth_mod.require_principal(
        credentials=creds, x_workspace_id=other, ws=WS
    )
    assert captured["requested"] == other
    assert p2.workspace_id == other


# ─────── prod hardening 2 (light): non-leaky brand-create existence ───────────
# Brand storage is one on-disk namespace until per-workspace storage lands, so a
# create must not become a cross-tenant existence oracle. A scoped caller who is
# not a member of an existing brand's workspace gets a generic "unavailable"; the
# owner (or an unscoped CLI) gets the precise message. No message leaks the path.


def test_create_existing_slug_scoped_non_member_is_generic(kits: Path) -> None:
    set_workspace_scope(WS, frozenset({"center-deep"}))  # excludes magic-unicorn
    with pytest.raises(FileExistsError) as ei:
        create_brand_from_draft("magic-unicorn", _DRAFT, kits_dir=kits)
    msg = str(ei.value)
    assert "unavailable" in msg
    assert "magic-unicorn" not in msg  # never confirms the slug exists elsewhere
    assert str(kits) not in msg  # never discloses the filesystem path


def test_create_existing_slug_owned_is_precise(kits: Path) -> None:
    set_workspace_scope(WS, frozenset({"magic-unicorn"}))
    with pytest.raises(FileExistsError) as ei:
        create_brand_from_draft("magic-unicorn", _DRAFT, kits_dir=kits)
    msg = str(ei.value)
    assert "already exists" in msg
    assert str(kits) not in msg


def test_create_existing_slug_unscoped_is_precise(kits: Path) -> None:
    with pytest.raises(FileExistsError) as ei:
        create_brand_from_draft("magic-unicorn", _DRAFT, kits_dir=kits)
    msg = str(ei.value)
    assert "already exists" in msg
    assert str(kits) not in msg


# ─────── prod hardening 3: MCP create DB-indexes under the workspace ──────────
# The HTTP MCP resolves a workspace per request, so a brand created via the MCP
# create tools must join its workspace catalog like the REST path does. The
# index is best effort (the boot import backstops a DB hiccup) and a no-op when
# unscoped (stdio / CLI), exactly like the REST _index_brand.


def test_mcp_index_noop_when_unscoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from brand_ops import mcp_server
    from brand_ops.db import tenancy

    calls = {"n": 0}

    async def fake_record(*a, **k):
        calls["n"] += 1

    monkeypatch.setattr(tenancy, "record_brand", fake_record)
    mcp_server._index_brand_best_effort(load_brand("magic-unicorn"))
    assert calls["n"] == 0


def test_mcp_index_records_when_scoped(
    monkeypatch: pytest.MonkeyPatch, scoped_kits: Path
) -> None:
    from brand_ops import mcp_server
    from brand_ops.db import tenancy

    seen: dict[str, str | None] = {}

    async def fake_record(workspace_id, **kw):
        seen["ws"] = workspace_id
        seen["slug"] = kw.get("slug")

    monkeypatch.setattr(tenancy, "record_brand", fake_record)
    set_workspace_scope(WS, frozenset({"magic-unicorn"}))
    mcp_server._index_brand_best_effort(load_brand("magic-unicorn"))
    assert seen.get("ws") == WS
    assert seen.get("slug") == "magic-unicorn"


def test_mcp_index_swallows_db_error(
    monkeypatch: pytest.MonkeyPatch, scoped_kits: Path
) -> None:
    from brand_ops import mcp_server
    from brand_ops.db import tenancy

    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(tenancy, "record_brand", boom)
    set_workspace_scope(WS, frozenset({"magic-unicorn"}))
    # Best effort: a DB failure must not raise (the boot import is the backstop).
    mcp_server._index_brand_best_effort(load_brand("magic-unicorn"))


# ─────── per-workspace on-disk isolation: two tenants, same brand slug ────────
# The enterprise property: brand files live at brand-kits/<workspace_id>/<slug>/,
# so two workspaces may hold the SAME slug without colliding and a read can never
# resolve into another tenant's subtree. This is the physical isolation beneath
# the allow-list visibility gate.

_WS1 = "019e5111-0000-7000-a000-000000000001"
_WS2 = "019e5222-0000-7000-a000-000000000002"


def _write_min_brand(path: Path, slug: str, display_name: str) -> None:
    """Write a minimal valid brand.yaml at path/<slug>/brand.yaml."""
    d = path / slug
    d.mkdir(parents=True)
    (d / "brand.yaml").write_text(
        f"brand:\n  slug: {slug}\n  display_name: {display_name}\n"
        "visual:\n  colors:\n    primary: '#112233'\n    accent: '#445566'\n"
    )


def test_two_workspaces_isolated_same_slug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "brand-kits"
    # The SAME slug 'acme' in two workspaces, with distinct files.
    _write_min_brand(root / _WS1, "acme", "Acme One")
    _write_min_brand(root / _WS2, "acme", "Acme Two")
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)

    # Scoped to WS1, 'acme' is WS1's brand; the read resolves WS1's subtree.
    set_workspace_scope(_WS1, frozenset({"acme"}))
    assert load_brand("acme").brand.display_name == "Acme One"
    assert list_brands() == ["acme"]
    brand_loader.reset_workspace_scope()

    # Scoped to WS2, the same slug resolves WS2's distinct file -- no collision,
    # no cross-tenant bleed.
    set_workspace_scope(_WS2, frozenset({"acme"}))
    assert load_brand("acme").brand.display_name == "Acme Two"
    brand_loader.reset_workspace_scope()


def test_workspace_subtree_does_not_see_other_tenant_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # WS1 owns 'alpha', WS2 owns 'beta'. Scoped to WS1, 'beta' is not even on
    # WS1's subtree, so it is unreadable regardless of the allow-list.
    root = tmp_path / "brand-kits"
    _write_min_brand(root / _WS1, "alpha", "Alpha")
    _write_min_brand(root / _WS2, "beta", "Beta")
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)

    set_workspace_scope(_WS1, frozenset({"alpha", "beta"}))  # allow-list even lists beta
    assert load_brand("alpha").brand.slug == "alpha"
    # beta is on WS2's subtree, not WS1's -> not found, even though allow-lists it.
    with pytest.raises(BrandNotFound):
        load_brand("beta")


# ─────── the flat -> per-workspace storage migration (non-destructive) ────────


def test_migrate_storage_copy_then_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from brand_ops import migrate_storage

    root = tmp_path / "brand-kits"
    # Pre-migration shape: a flat brand (with a generated asset) + the global feed.
    _write_min_brand(root, "acme", "Acme")
    (root / "acme" / "logo.png").write_bytes(b"png-bytes")
    (root / ".activity.jsonl").write_text('{"id":"1","action":"made"}\n')
    monkeypatch.setenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", WS)

    # Dry run changes nothing.
    monkeypatch.setattr("sys.argv", ["m", "--dry-run", "--root", str(root)])
    migrate_storage.main()
    assert (root / "acme").is_dir()
    assert not (root / WS).exists()

    # Copy builds the per-workspace layout beside the flat one (non-destructive).
    monkeypatch.setattr("sys.argv", ["m", "--root", str(root)])
    migrate_storage.main()
    assert (root / WS / "acme" / "brand.yaml").exists()
    assert (root / WS / "acme" / "logo.png").read_bytes() == b"png-bytes"
    assert (root / WS / ".activity.jsonl").exists()
    assert (root / "acme").is_dir()  # flat copy still present until cleanup

    # Idempotent: a second copy is a no-op (nothing new), flat still intact.
    migrate_storage.main()
    assert (root / "acme").is_dir()

    # Cleanup removes the verified flat copies.
    monkeypatch.setattr("sys.argv", ["m", "--cleanup", "--root", str(root)])
    migrate_storage.main()
    assert not (root / "acme").exists()
    assert not (root / ".activity.jsonl").exists()
    assert (root / WS / "acme" / "brand.yaml").exists()


def test_migrate_storage_honors_recorded_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A flat brand whose brand.yaml records a workspace goes to THAT workspace,
    # not the bootstrap one.
    from brand_ops import migrate_storage

    root = tmp_path / "brand-kits"
    d = root / "acme"
    d.mkdir(parents=True)
    (d / "brand.yaml").write_text(
        f"brand:\n  slug: acme\n  display_name: Acme\n  workspace_id: {_WS2}\n"
        "visual:\n  colors:\n    primary: '#112233'\n    accent: '#445566'\n"
    )
    monkeypatch.setenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", WS)
    monkeypatch.setattr("sys.argv", ["m", "--root", str(root)])
    migrate_storage.main()
    # Placed under its recorded workspace _WS2, not the bootstrap WS.
    assert (root / _WS2 / "acme" / "brand.yaml").exists()
    assert not (root / WS / "acme").exists()
