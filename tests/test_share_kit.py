"""The per-brand social-share / link-preview kit: the Open Graph + Twitter tags
a brand pastes into its OWN site, built from brand identity.

Covers build_share_meta (placeholders when the site is unknown, filling from a
canonical URL / the brand's website / an explicit image URL, HTML escaping, the
title), plus the GET /brands/{slug}/share-preview route shape and 404.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.brand_loader import load_brand
from brand_ops.share_meta import PLACEHOLDER_SITE, build_share_meta

_OG_TWITTER_KEYS = {
    "og:type",
    "og:title",
    "og:description",
    "og:url",
    "og:image",
    "og:image:width",
    "og:image:height",
    "twitter:card",
    "twitter:title",
    "twitter:image",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def _brand():
    return load_brand("magic-unicorn")


def test_placeholders_when_site_unknown():
    b = _brand()
    b.brand.website = None
    meta = build_share_meta(b)
    assert "og:url" in meta["placeholders"]
    assert "og:image" in meta["placeholders"]
    assert PLACEHOLDER_SITE in meta["html"]
    keys = {t["key"] for t in meta["tags"]}
    assert _OG_TWITTER_KEYS <= keys
    assert meta["tags"][0]["attr"] == "property"
    assert any(t["attr"] == "name" for t in meta["tags"])  # twitter tags present


def test_canonical_url_fills_url_and_defaults_image():
    b = _brand()
    b.brand.website = None
    meta = build_share_meta(b, canonical_url="https://magicunicorn.tech/")
    assert meta["url"] == "https://magicunicorn.tech"  # trailing slash trimmed
    assert meta["image_url"] == "https://magicunicorn.tech/og.png"  # defaulted
    assert meta["placeholders"] == []


def test_website_field_fills_url():
    b = _brand()
    b.brand.website = "https://example-brand.test"
    meta = build_share_meta(b)
    assert meta["url"] == "https://example-brand.test"
    assert meta["placeholders"] == []


def test_explicit_image_url_used_verbatim():
    b = _brand()
    meta = build_share_meta(
        b, canonical_url="https://x.test", image_url="https://cdn.x.test/share.png"
    )
    assert meta["image_url"] == "https://cdn.x.test/share.png"


def test_html_escapes_content():
    b = _brand()
    b.brand.display_name = 'A & B "Co"'
    meta = build_share_meta(b)
    assert "&amp;" in meta["html"]
    assert "&quot;" in meta["html"]
    # the raw quote+name+quote never leaks unescaped into an attribute value
    assert '"Co"' not in meta["html"]


def test_title_joins_display_and_tagline():
    b = _brand()
    b.brand.display_name = "Acme"
    b.brand.tagline = "We make things"
    meta = build_share_meta(b)
    assert meta["title"] == "Acme - We make things"


def test_description_collapses_and_trims():
    b = _brand()
    b.brand.description = "word " * 100  # long, whitespace-heavy
    meta = build_share_meta(b)
    assert len(meta["description"]) <= 203  # 200 + the "..." marker
    assert "\n" not in meta["description"]


def test_route_returns_kit(client: TestClient):
    resp = client.get("/api/brands/magic-unicorn/share-preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["brand"] == "magic-unicorn"
    assert body["html"].startswith("<meta ")
    assert {t["key"] for t in body["tags"]} >= _OG_TWITTER_KEYS
    # image is either the brand's og_image record or null (none generated yet)
    assert body["image"] is None or "asset_url" in body["image"]


def test_route_query_params_fill_tags(client: TestClient):
    resp = client.get(
        "/api/brands/magic-unicorn/share-preview",
        params={"canonical_url": "https://magicunicorn.tech", "image_url": "https://magicunicorn.tech/share.png"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"] == "https://magicunicorn.tech"
    assert body["image_url"] == "https://magicunicorn.tech/share.png"
    assert body["placeholders"] == []


def test_route_unknown_brand_404(client: TestClient):
    resp = client.get("/api/brands/no-such-brand/share-preview")
    assert resp.status_code == 404
