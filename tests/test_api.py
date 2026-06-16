"""REST API tests.

These exercise the FastAPI surface with the in-process TestClient and no
network: auth is off by default (AUTH_REQUIRED unset), so every request resolves
to the anonymous principal and no token or Keycloak is needed. Generation is
only tested in dry-run (prompt-preview), so no provider SDK is called and no
image API is hit.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Make auth-off explicit and deterministic regardless of the ambient env.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "brand-ops"
    assert "version" in body
    # Auth is bypassed in tests and local dev.
    assert body["auth_required"] is False


def test_list_brands(client: TestClient) -> None:
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    body = resp.json()
    assert "brands" in body
    brands = body["brands"]
    assert isinstance(brands, list)
    # The repo seeds at least the magic-unicorn brand.
    slugs = {b["slug"] for b in brands}
    assert "magic-unicorn" in slugs
    for b in brands:
        assert b["slug"]
        assert b["display_name"]


def test_list_brands_carries_canonical_mark_url(client: TestClient) -> None:
    # Each brand declares a canonical mark; the list exposes it as a ready URL
    # into /api/assets/file. magic-unicorn's mark is its unicorn.svg logo.
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    by_slug = {b["slug"]: b for b in resp.json()["brands"]}
    mu = by_slug["magic-unicorn"]
    assert mu["mark_url"]
    assert "unicorn.svg" in mu["mark_url"]
    assert mu["mark_url"].startswith("/api/assets/file?brand=magic-unicorn")


def test_list_brands_carries_colors(client: TestClient) -> None:
    # The list endpoint carries each brand's resolved palette (the same colors
    # BrandDetail serves under visual.colors), so the brand switcher can theme by
    # the real colors without a per-brand detail fetch. Every listed brand has a
    # non-null primary.
    resp = client.get("/api/brands")
    assert resp.status_code == 200
    brands = resp.json()["brands"]
    for b in brands:
        assert b["colors"] is not None, f"{b['slug']} has no colors"
        assert b["colors"]["primary"].startswith("#")
    # The list color matches what the detail endpoint reports for the same brand.
    by_slug = {b["slug"]: b for b in brands}
    mu = by_slug["magic-unicorn"]
    detail = client.get("/api/brands/magic-unicorn").json()
    assert mu["colors"]["primary"] == detail["visual"]["colors"]["primary"]
    assert mu["colors"]["accent"] == detail["visual"]["colors"]["accent"]


def test_brand_detail(client: TestClient) -> None:
    resp = client.get("/api/brands/magic-unicorn")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "magic-unicorn"
    assert body["display_name"] == "Magic Unicorn"
    assert body["visual"]["colors"]["primary"].startswith("#")
    assert isinstance(body["visual"]["style_keywords"], list)
    assert isinstance(body["prompt_assets"], list)


def test_brand_detail_missing_is_404(client: TestClient) -> None:
    resp = client.get("/api/brands/no-such-brand")
    assert resp.status_code == 404


def test_asset_types(client: TestClient) -> None:
    resp = client.get("/api/asset-types")
    assert resp.status_code == 200
    body = resp.json()
    assert "asset_types" in body
    types = body["asset_types"]
    assert isinstance(types, list)
    keys = {t["key"] for t in types}
    # The registry includes at least these.
    assert {"logo", "og_image"} <= keys
    for t in types:
        assert t["width"] > 0
        assert t["height"] > 0
        assert t["default_backend"]


def test_prompt_preview_dry_run(client: TestClient) -> None:
    resp = client.post(
        "/api/prompt-preview",
        json={"brand": "magic-unicorn", "asset": "og_image", "topic": "investor update"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand"] == "magic-unicorn"
    assert body["asset"] == "og_image"
    # og_image defaults to gemini at 1200x630 per the asset registry.
    assert body["backend"] == "gemini"
    assert body["width"] == 1200
    assert body["height"] == 630
    assert isinstance(body["prompt"], str) and body["prompt"]
    # The topic flows into the composed prompt.
    assert "investor update" in body["prompt"]
    assert isinstance(body["warnings"], list)
    assert isinstance(body["missing_refs"], list)
    assert "planned_path" in body


def test_prompt_preview_unknown_asset_is_422(client: TestClient) -> None:
    resp = client.post(
        "/api/prompt-preview",
        json={"brand": "magic-unicorn", "asset": "not-an-asset"},
    )
    assert resp.status_code == 422


def test_prompt_preview_unknown_brand_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/prompt-preview",
        json={"brand": "no-such-brand", "asset": "og_image"},
    )
    assert resp.status_code == 404


def test_served_svg_asset_is_forced_download(client: TestClient) -> None:
    # An SVG can carry <script>, so the file route must serve it as a download,
    # never inline, and tell the browser not to sniff the bytes into HTML. The
    # seeded magic-unicorn mark (unicorn.svg) is a real on-disk SVG to serve.
    resp = client.get(
        "/api/assets/file",
        params={
            "brand": "magic-unicorn",
            "asset": "logo",
            "file": "unicorn.svg",
            "kind": "imported",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"].startswith("attachment")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_served_raster_asset_stays_inline(client: TestClient) -> None:
    # A raster image must keep serving inline so the GUI's <img> tags render it:
    # no attachment disposition. magic-unicorn ships a PNG logo on disk.
    resp = client.get(
        "/api/assets/file",
        params={
            "brand": "magic-unicorn",
            "asset": "logo",
            "file": "magic-unicorn-logo.png",
            "kind": "imported",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "content-disposition" not in resp.headers
    # nosniff is harmless for an <img> and still hardens the raster path.
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_assets_catalog_shape(client: TestClient) -> None:
    # No generation has run, so the catalog is empty, but the endpoint and its
    # shape must hold (and the brand filter must be accepted).
    resp = client.get("/api/assets", params={"brand": "magic-unicorn"})
    assert resp.status_code == 200
    body = resp.json()
    assert "assets" in body
    assert isinstance(body["assets"], list)


def test_auth_bypassed_by_default(client: TestClient) -> None:
    # With AUTH_REQUIRED unset, protected routes answer without any token.
    assert os.getenv("AUTH_REQUIRED") in (None, "")
    resp = client.get("/api/brands")
    assert resp.status_code == 200


def test_auth_required_blocks_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Flip the flag on: a protected route now demands a bearer and 401s without
    # one. This stays offline: the missing-credential branch never reaches a
    # network call.
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    c = TestClient(create_app())
    resp = c.get("/api/brands")
    assert resp.status_code == 401
    # Health stays open even with auth required.
    assert c.get("/health").status_code == 200
