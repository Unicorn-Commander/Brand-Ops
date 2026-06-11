"""Tests for the assembled brand guidelines document (the brand book).

These exercise the FastAPI surface with the in-process TestClient and no
network: auth is off (AUTH_REQUIRED unset), so every request resolves to the
anonymous principal. The endpoint only reads the on-disk catalog the repo seeds,
so no provider SDK is called and no spend occurs.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Make auth-off explicit and deterministic regardless of the ambient env.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def test_guidelines_assembles_the_brand_book(client: TestClient) -> None:
    resp = client.get("/api/brands/magic-unicorn/guidelines")
    assert resp.status_code == 200
    body = resp.json()

    # Identity header.
    assert body["brand"]["slug"] == "magic-unicorn"
    assert body["brand"]["display_name"] == "Magic Unicorn"

    # Colors: reused token output, each carrying name + hex value + rgb triple.
    colors = body["colors"]
    assert isinstance(colors, list) and colors
    primary = colors[0]
    assert primary["name"] == "brand/primary"
    assert primary["value"].startswith("#")
    assert len(primary["rgb"]) == 3

    # Typography tokens are present (display/body/mono).
    typography = body["typography"]
    assert isinstance(typography, list) and typography
    assert {t["name"] for t in typography} >= {"font/display", "font/body", "font/mono"}

    # Rules manifest is the deterministic rule set.
    rules = body["rules"]
    assert rules["brand"] == "magic-unicorn"
    assert isinstance(rules["allowed_colors"], list) and rules["allowed_colors"]
    assert "color_tolerance" in rules
    assert "banned_terms" in rules
    assert "voice_tone" in rules
    assert "manual_rules" in rules

    # Voice section.
    assert isinstance(body["voice"]["tone"], list)
    assert isinstance(body["voice"]["forbidden_phrases"], list)

    # Logo section: the canonical mark resolves to the unicorn.svg URL.
    assert body["logo"]["mark_url"]
    assert "unicorn.svg" in body["logo"]["mark_url"]
    assert isinstance(body["logo"]["lockups"], list)

    # Asset groups: non-empty, each in registry shape with a label and a count
    # that matches the number of items.
    groups = body["asset_groups"]
    assert isinstance(groups, list) and groups
    for group in groups:
        assert group["asset_type"]
        assert group["label"]
        assert group["count"] == len(group["items"])

    # The route does not call a clock.
    assert body["generated_at"] is None


def test_guidelines_unknown_brand_is_404(client: TestClient) -> None:
    resp = client.get("/api/brands/does-not-exist/guidelines")
    assert resp.status_code == 404
