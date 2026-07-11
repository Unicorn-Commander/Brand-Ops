"""Tests for the new_brand MCP tool: honest creation, never template junk.

new_brand is a thin adapter over the shared creation cores (clone_brand for a
parented create, create_brand_from_draft for a parentless one), so a brand it
creates must load valid, carry the caller's copy verbatim, start with no mark,
and never contain a REPLACE-token placeholder. The tool resolves against
DEFAULT_KITS_DIR (no kits_dir parameter), so these tests track and remove every
brand directory they create, leaving the repo kits exactly as found.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator

import pytest
import yaml

from brand_ops import mcp_server
from brand_ops.brand_loader import DEFAULT_KITS_DIR, load_brand

PARENT = "magic-unicorn"
PARENT_PRIMARY = "#b66eff"


@pytest.fixture
def cleanup_real_kits() -> Iterator[list[str]]:
    """Track brand slugs created in the real brand-kits; remove them after."""
    created: list[str] = []
    try:
        yield created
    finally:
        for slug in created:
            shutil.rmtree(DEFAULT_KITS_DIR / slug, ignore_errors=True)


def test_new_brand_parented_is_honest_child(cleanup_real_kits: list[str]) -> None:
    cleanup_real_kits.append("nb-child")
    result = mcp_server.new_brand(
        "nb-child",
        "NB Child",
        parent=PARENT,
        tagline="Honest one-liner.",
        description="An honest description of the brand.",
    )
    assert "error" not in result
    # The tool's return contract: paths plus the identity echoes.
    assert result["slug"] == "nb-child"
    assert result["parent"] == PARENT
    assert result["tagline"] == "Honest one-liner."
    assert result["description"] == "An honest description of the brand."
    assert result["created"].endswith("nb-child")
    assert result["brand_yaml"].endswith("brand.yaml")

    # The written file carries no placeholder junk anywhere and loads valid.
    text = (DEFAULT_KITS_DIR / "nb-child" / "brand.yaml").read_text()
    assert "REPLACE" not in text
    assert "Replace with" not in text
    raw = yaml.safe_load(text)
    assert raw["brand"]["slug"] == "nb-child"
    assert raw["brand"]["tagline"] == "Honest one-liner."
    assert "mark" not in raw["brand"]

    brand = load_brand("nb-child")
    assert brand.brand.mark is None
    # A child inherits the parent's visual through the loader merge.
    assert brand.visual.colors.primary == PARENT_PRIMARY


def test_new_brand_parentless_uses_starter_palette(cleanup_real_kits: list[str]) -> None:
    cleanup_real_kits.append("nb-solo")
    result = mcp_server.new_brand("nb-solo", "NB Solo", tagline="Solo line.")
    assert "error" not in result
    assert result["parent"] is None
    assert result["tagline"] == "Solo line."

    text = (DEFAULT_KITS_DIR / "nb-solo" / "brand.yaml").read_text()
    assert "REPLACE" not in text
    assert "Replace with" not in text

    brand = load_brand("nb-solo")
    assert brand.brand.display_name == "NB Solo"
    assert brand.brand.parent is None
    assert brand.brand.tagline == "Solo line."
    assert brand.brand.mark is None
    # The neutral "not dressed yet" starter palette, not a guessed identity.
    assert brand.visual.colors.primary == "#000000"
    assert brand.visual.colors.accent == "#000000"
    assert brand.visual.colors.background == "#0a0a0a"
    assert brand.visual.colors.foreground == "#fafafa"
    assert brand.visual.colors.muted == "#404040"
    # No placeholder style keywords; prompts are the real default templates, so
    # the brand is immediately generatable.
    assert brand.visual.style_keywords == []
    assert "logo" in brand.prompts
    assert "{brand_context}" in brand.prompts["logo"].base


def test_new_brand_without_copy_writes_no_tagline(cleanup_real_kits: list[str]) -> None:
    cleanup_real_kits.append("nb-plain")
    result = mcp_server.new_brand("nb-plain", "NB Plain")
    assert "error" not in result
    assert result["tagline"] is None
    assert result["description"] is None
    raw = yaml.safe_load((DEFAULT_KITS_DIR / "nb-plain" / "brand.yaml").read_text())
    # Honest absence: no key at all, never placeholder text.
    assert "tagline" not in raw["brand"]
    assert "description" not in raw["brand"]


def test_new_brand_duplicate_slug_is_error(cleanup_real_kits: list[str]) -> None:
    cleanup_real_kits.append("nb-dup")
    first = mcp_server.new_brand("nb-dup", "NB Dup")
    assert "error" not in first
    again = mcp_server.new_brand("nb-dup", "NB Dup 2")
    assert "error" in again


def test_new_brand_invalid_slug_is_error() -> None:
    result = mcp_server.new_brand("Bad Slug!", "Bad")
    assert "error" in result
    assert not (DEFAULT_KITS_DIR / "Bad Slug!").exists()


def test_new_brand_unknown_parent_is_error() -> None:
    result = mcp_server.new_brand("nb-orphan", "Orphan", parent="no-such-brand")
    assert "error" in result
    assert not (DEFAULT_KITS_DIR / "nb-orphan").exists()
