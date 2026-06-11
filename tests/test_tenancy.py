"""Org-scoped multi-tenancy tests: the contextvar flow, the dogfood-open case,
and cross-tenant isolation.

Brand scoping is centralized in ``brand_loader`` via the ``current_org``
contextvar: ``require_principal`` sets it to the caller's org for the request,
and ``list_brands`` / ``load_brand`` filter against it. These tests cover three
things the design must guarantee:

* the open dogfood (AUTH_REQUIRED unset -> anonymous -> default org
  ``magic-unicorn``) still sees every seed brand, so nothing regresses;
* a request scoped to a *different* org sees none of the magic-unicorn brands,
  and a cross-tenant read is a 404 (``BrandNotFound``), never a 403 leak;
* a brand created while scoped to a tenant is owned by that tenant and is then
  invisible to another tenant.

The contextvar is reset in an autouse fixture so org state never leaks between
tests (or out to the rest of the suite, which runs unscoped).
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
    list_brands,
    load_brand,
    set_current_org,
)

# The five seed brands shipped in brand-kits/. None carries an explicit ``org``,
# so each loads as the default org ``magic-unicorn`` -- the same org the open
# dogfood's anonymous principal is scoped to.
SEED_BRANDS = {
    "center-deep",
    "magic-unicorn",
    "majiks",
    "majiks-studio",
    "unicorn-commander",
}
DEFAULT_ORG = "magic-unicorn"


@pytest.fixture(autouse=True)
def _reset_org() -> Iterator[None]:
    """Clear the request-scoped org before and after every test.

    Without this, a test that sets ``current_org`` would leave the scope set for
    whatever test runs next in the same thread (the contextvar default is None =
    unscoped, which the rest of the suite relies on).
    """
    brand_loader.reset_current_org()
    try:
        yield
    finally:
        brand_loader.reset_current_org()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off (the dogfood posture): every request resolves to the anonymous
    # principal, whose org is the default. No token, no Keycloak, no network.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    """A throwaway copy of the repo brand-kits, so writes never touch the repo."""
    dest = tmp_path / "brand-kits"
    shutil.copytree(DEFAULT_KITS_DIR, dest)
    return dest


# ─────────────────── (a) dogfood: open access sees all seeds ────────────────


def test_dogfood_anonymous_sees_all_seed_brands(client: TestClient) -> None:
    # AUTH_REQUIRED unset -> anonymous principal -> default org "magic-unicorn".
    # The seed brands all default to that org, so the open dogfood lists them
    # all: org scoping must not hide anything on the open path.
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    slugs = {b["slug"] for b in resp.json()["brands"]}
    assert SEED_BRANDS <= slugs, f"missing seed brands: {SEED_BRANDS - slugs}"


def test_dogfood_brand_detail_is_200(client: TestClient) -> None:
    # A specific seed brand resolves for the anonymous (default-org) caller.
    resp = client.get("/api/brands/center-deep")
    assert resp.status_code == 200
    assert resp.json()["slug"] == "center-deep"


@pytest.mark.asyncio
async def test_require_principal_sets_current_org_in_context() -> None:
    # The contextvar flow itself, observed in the same context the dependency
    # runs in (the TestClient runs each request in its own context, so the set
    # is correctly isolated there and cannot be observed from the test thread --
    # which is exactly the per-request isolation we want). Here we await the
    # dependency directly: with no credentials and auth off, it returns the
    # anonymous principal at the default org AND publishes that org to
    # current_org for the surrounding context.
    from brand_ops.api.auth import require_principal

    assert brand_loader.get_current_org() is None
    principal = await require_principal(credentials=None)
    assert principal.anonymous is True
    assert principal.org == DEFAULT_ORG
    assert brand_loader.get_current_org() == DEFAULT_ORG


# ─────────────────────────── unscoped == global ────────────────────────────


def test_unscoped_lists_every_brand() -> None:
    # With current_org None (the CLI / MCP / test default), scoping is off and
    # list_brands returns the full global listing -- backward compatible.
    assert brand_loader.get_current_org() is None
    assert SEED_BRANDS <= set(list_brands())


# ──────────────── (b) isolation: a different org sees nothing ───────────────


def test_other_org_lists_no_seed_brands() -> None:
    # Scope the "request" to a foreign tenant: none of the magic-unicorn-owned
    # seed brands are visible.
    set_current_org("other-co")
    visible = set(list_brands())
    assert visible & SEED_BRANDS == set(), f"leaked seed brands: {visible & SEED_BRANDS}"


def test_other_org_cross_tenant_read_is_brandnotfound() -> None:
    # A cross-tenant read of a known seed brand is a 404, not a 403: the foreign
    # tenant cannot even tell the brand exists.
    set_current_org("other-co")
    with pytest.raises(BrandNotFound):
        load_brand("magic-unicorn")


def test_same_org_still_resolves() -> None:
    # Sanity: scoping to the brand's own org still resolves it (the guard is an
    # equality check, not a blanket deny).
    set_current_org(DEFAULT_ORG)
    brand = load_brand("magic-unicorn")
    assert brand.brand.slug == "magic-unicorn"
    assert brand.brand.org == DEFAULT_ORG


# ─────────── (c) a brand created while scoped is owned by that tenant ───────


_DRAFT = {
    "palette": {"primary": "#123456", "accent": "#abcdef"},
    "voice_tone": ["bold"],
    "style_keywords": ["clean"],
    "display_name": "Acme Brand",
}


def test_created_brand_is_owned_by_the_scoped_org(kits: Path) -> None:
    # Create a brand while scoped to "acme": the written brand.yaml records
    # brand.org == "acme".
    set_current_org("acme")
    brand = create_brand_from_draft("acme-brand", _DRAFT, kits_dir=kits)
    assert brand.brand.org == "acme"


def test_created_brand_is_invisible_to_another_tenant(kits: Path) -> None:
    # The brand acme created is hidden from magic-unicorn: not in its listing,
    # and a direct read 404s. (Both tenants point at the same kits tree, so this
    # is genuine org isolation, not directory isolation.)
    set_current_org("acme")
    create_brand_from_draft("acme-brand", _DRAFT, kits_dir=kits)

    set_current_org(DEFAULT_ORG)
    assert "acme-brand" not in list_brands(kits_dir=kits)
    with pytest.raises(BrandNotFound):
        load_brand("acme-brand", kits_dir=kits)

    # And acme still sees its own brand in the same tree.
    set_current_org("acme")
    assert "acme-brand" in list_brands(kits_dir=kits)
