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


def test_scoped_to_subset_lists_only_those() -> None:
    set_workspace_scope(WS, frozenset({"center-deep", "majiks"}))
    assert set(list_brands()) == {"center-deep", "majiks"}


def test_scoped_cross_tenant_read_is_brandnotfound() -> None:
    # magic-unicorn is not in this workspace's allow-list -> 404, never a 403.
    set_workspace_scope(WS, frozenset({"center-deep"}))
    with pytest.raises(BrandNotFound):
        load_brand("magic-unicorn")


def test_scoped_in_set_still_resolves() -> None:
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


def test_allow_brand_adds_to_scope() -> None:
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
