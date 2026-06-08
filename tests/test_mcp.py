"""Tests for the Brand-Ops MCP server.

These exercise the read tools only. They never call live generation, so no
backend, network, or spend is involved. FastMCP's @mcp.tool() returns the
underlying function unchanged, so each tool is callable directly here.
"""
from __future__ import annotations

from brand_ops import mcp_server


def test_module_imports_and_names_server() -> None:
    assert mcp_server.mcp.name == "brand-ops"


def test_list_brands_shape() -> None:
    result = mcp_server.list_brands()
    assert "error" not in result
    assert isinstance(result["brands"], list)
    assert result["count"] == len(result["brands"])
    # The seeded kit set includes magic-unicorn.
    assert "magic-unicorn" in result["brands"]


def test_list_asset_types_shape() -> None:
    result = mcp_server.list_asset_types()
    assert "error" not in result
    types = result["asset_types"]
    assert isinstance(types, list)
    assert result["count"] == len(types)

    keys = {t["key"] for t in types}
    assert "og_image" in keys
    assert "logo" in keys

    for spec in types:
        assert set(spec) == {"key", "label", "size", "default_backend", "transparent"}
        assert isinstance(spec["size"], list)
        assert len(spec["size"]) == 2
        assert all(isinstance(n, int) for n in spec["size"])
        assert isinstance(spec["transparent"], bool)


def test_get_brand_identity_shape() -> None:
    result = mcp_server.get_brand("magic-unicorn")
    assert "error" not in result
    assert result["slug"] == "magic-unicorn"
    assert result["display_name"] == "Magic Unicorn"
    assert result["colors"]["primary"].startswith("#")
    assert "primary_font" in result["fonts"]
    assert isinstance(result["style_keywords"], list)
    assert isinstance(result["voice"]["forbidden_phrases"], list)
    assert "declared" in result["references"]


def test_get_brand_unknown_returns_error_not_raise() -> None:
    result = mcp_server.get_brand("does-not-exist")
    assert "error" in result
    assert result["slug"] == "does-not-exist"


def test_build_prompt_og_image_magic_unicorn() -> None:
    result = mcp_server.build_prompt(
        "magic-unicorn",
        "og_image",
        topic="self-hostable AI infrastructure",
    )
    assert "error" not in result
    assert result["brand"] == "magic-unicorn"
    assert result["asset"] == "og_image"
    assert isinstance(result["prompt"], str)
    assert result["prompt"]  # non-empty composed prompt
    # og_image defaults to gemini at 1200x630 per the asset registry.
    assert result["backend"] == "gemini"
    assert result["size"] == [1200, 630]
    assert result["transparent"] is False
    assert isinstance(result["warnings"], list)
    assert isinstance(result["missing_references"], list)
    assert result["planned_output"].endswith(".png")


def test_build_prompt_unknown_asset_returns_error() -> None:
    result = mcp_server.build_prompt("magic-unicorn", "not_a_real_asset")
    assert "error" in result


def test_list_generated_assets_shape() -> None:
    result = mcp_server.list_generated_assets(brand="magic-unicorn")
    assert "error" not in result
    assert isinstance(result["assets"], list)
    assert result["count"] == len(result["assets"])
