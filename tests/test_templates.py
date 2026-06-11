"""Template registry tests: the locked, data-driven layout set.

These exercise GET /api/templates with the in-process TestClient (auth off, no
network, no generation) and the matching MCP list_templates tool. They assert the
shared contract holds: the five templates ship, every color references a known
brand color token and every font a known font role (never raw hex), dimensions
are positive, and the OG card is the 1200x630 standard.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops import mcp_server
from brand_ops.api.app import create_app
from brand_ops.templates import COLOR_TOKENS, FONT_ROLES, list_templates

# The exact set the contract ships, by id.
EXPECTED_IDS = {
    "og_hero",
    "social_square",
    "social_story",
    "quote_card",
    "feature_announcement",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Make auth-off explicit and deterministic regardless of the ambient env.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def _assert_template_contract(templates: list[dict]) -> None:
    """Assert the full TemplateDef + Slot contract over a list of template dicts."""
    assert len(templates) == 5
    assert {t["id"] for t in templates} == EXPECTED_IDS

    for tpl in templates:
        # Top-level shape.
        assert tpl["id"]
        assert tpl["label"]
        assert tpl["asset_type"] in {"og_image", "social_card"}
        # Dimensions are positive.
        assert isinstance(tpl["width"], int) and tpl["width"] > 0
        assert isinstance(tpl["height"], int) and tpl["height"] > 0

        # Background is a known kind; solid carries a color token, gradient does not.
        bg = tpl["background"]
        assert bg["kind"] in {"brand-gradient", "solid"}
        if bg["kind"] == "solid":
            assert bg["color"] in COLOR_TOKENS
        else:
            assert bg["color"] is None

        # Slots: every slot is positioned by 0..1 fractions, text slots reference
        # only known token roles, mark slots carry no typesetting.
        assert tpl["slots"], f"{tpl['id']} has no slots"
        for slot in tpl["slots"]:
            assert slot["key"]
            assert slot["kind"] in {"text", "mark"}
            assert 0.0 <= slot["x"] <= 1.0
            assert 0.0 <= slot["y"] <= 1.0
            assert 0.0 < slot["w"] <= 1.0

            if slot["kind"] == "text":
                # A text slot's color is a known color token role, never raw hex.
                assert slot["color"] in COLOR_TOKENS
                assert not slot["color"].startswith("#")
                # Its font is a known font role.
                assert slot["font"] in FONT_ROLES
                # Its size is a positive number of px.
                assert isinstance(slot["size"], (int, float)) and slot["size"] > 0
                assert slot["align"] in {"left", "center", "right"}
            else:  # mark
                # The mark is locked: it carries no editable text typesetting.
                assert slot["color"] is None
                assert slot["font"] is None
                assert slot["size"] is None
                assert slot["default"] is None


def test_get_templates_returns_the_five(client: TestClient) -> None:
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert "templates" in body
    _assert_template_contract(body["templates"])


def test_og_card_is_1200x630(client: TestClient) -> None:
    resp = client.get("/api/templates")
    by_id = {t["id"]: t for t in resp.json()["templates"]}
    og = by_id["og_hero"]
    assert og["asset_type"] == "og_image"
    assert og["width"] == 1200
    assert og["height"] == 630
    # The gradient hero references only token names, including a mark slot.
    assert og["background"]["kind"] == "brand-gradient"
    kinds = {s["kind"] for s in og["slots"]}
    assert "mark" in kinds and "text" in kinds


def test_expected_canvas_sizes(client: TestClient) -> None:
    # The square is 1080x1080, the story is 1080x1920, the feature card 1200x630.
    by_id = {t["id"]: t for t in client.get("/api/templates").json()["templates"]}
    assert (by_id["social_square"]["width"], by_id["social_square"]["height"]) == (
        1080,
        1080,
    )
    assert (by_id["social_story"]["width"], by_id["social_story"]["height"]) == (
        1080,
        1920,
    )
    assert (by_id["quote_card"]["width"], by_id["quote_card"]["height"]) == (1080, 1080)
    assert (
        by_id["feature_announcement"]["width"],
        by_id["feature_announcement"]["height"],
    ) == (1200, 630)


def test_quote_card_is_neutral_solid_background(client: TestClient) -> None:
    # The quote card uses a solid surface token, not the brand gradient, so long
    # copy stays legible.
    by_id = {t["id"]: t for t in client.get("/api/templates").json()["templates"]}
    quote = by_id["quote_card"]
    assert quote["background"]["kind"] == "solid"
    assert quote["background"]["color"] == "surface/background"
    # It has an editable quote and an attribution text slot.
    keys = {s["key"] for s in quote["slots"]}
    assert {"quote", "attribution"} <= keys


def test_helper_matches_route(client: TestClient) -> None:
    # The list_templates() helper is the single source the route serves.
    assert client.get("/api/templates").json()["templates"] == list_templates()


def test_mcp_list_templates_matches_route(client: TestClient) -> None:
    # The MCP read tool returns the same set, never raising across the boundary.
    result = mcp_server.list_templates()
    assert "error" not in result
    assert result["count"] == 5
    _assert_template_contract(result["templates"])
    assert result["templates"] == client.get("/api/templates").json()["templates"]
