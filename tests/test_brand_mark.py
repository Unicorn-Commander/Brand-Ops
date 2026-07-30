"""Tests for the auto-mark writer (brand_mark.maybe_set_brand_mark) and the
approve-path wiring that calls it.

The helper-level tests run against throwaway tmp kits so nothing real is
touched. The approve-path tests exercise POST /api/assets/approve end to end,
which resolves against DEFAULT_KITS_DIR, so they create a throwaway brand in
the real kits and remove it (plus everything it contains) on teardown --
matching the convention in test_clone.py.
"""
from __future__ import annotations

import base64
import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from brand_ops import mcp_server
from brand_ops.api.app import create_app
from brand_ops.brand_loader import DEFAULT_KITS_DIR, load_brand
from brand_ops.brand_mark import maybe_set_brand_mark

# A 1x1 transparent PNG (same fixture test_upload.py uses).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

PLAIN_YAML = """brand:
  slug: markless
  display_name: Markless
  parent: null
visual:
  colors:
    primary: "#112233"
    accent: "#445566"
"""

# A hand-authored kit shape: banner comment, trailing comment on the anchor
# line, a stray column-zero comment inside the block, and a block-scalar
# description. All of it must survive the insert byte for byte.
COMMENTED_YAML = """# Hand-authored kit. This banner comment must survive an auto-mark.
brand:
  slug: commented
  display_name: Commented   # trailing comment on the anchor line
  tagline: Real tagline.
  parent: null
  description: |
    Multi-line description that
    must stay a block scalar.
# a stray column-zero comment inside the block
  website: https://example.com
visual:
  colors:
    primary: "#112233"   # keep me
    accent: "#445566"
"""


def _kit(tmp_path: Path, slug: str, text: str) -> Path:
    kit = tmp_path / slug
    kit.mkdir(parents=True)
    (kit / "brand.yaml").write_text(text)
    return tmp_path


# ─────────────────────────── Helper: maybe_set_brand_mark ───────────────────


def test_sets_mark_when_absent(tmp_path: Path) -> None:
    root = _kit(tmp_path, "markless", PLAIN_YAML)
    ok = maybe_set_brand_mark("markless", "logo", "logo-1.png", "imported", kits_dir=root)
    assert ok is True
    raw = yaml.safe_load((root / "markless" / "brand.yaml").read_text())
    assert raw["brand"]["mark"] == {
        "asset_type": "logo",
        "file": "logo-1.png",
        "kind": "imported",
    }
    # The loader serves it: the same object _mark_url builds the card URL from.
    brand = load_brand("markless", kits_dir=root)
    assert brand.brand.mark is not None
    assert brand.brand.mark.file == "logo-1.png"
    assert brand.brand.mark.kind == "imported"


def test_preserves_comments_and_block_scalars(tmp_path: Path) -> None:
    root = _kit(tmp_path, "commented", COMMENTED_YAML)
    ok = maybe_set_brand_mark("commented", "logo", "the mark.png", "generated", kits_dir=root)
    assert ok is True
    text = (root / "commented" / "brand.yaml").read_text()
    # Every comment survives the textual insert (a parse-and-redump would not).
    assert "# Hand-authored kit." in text
    assert "# trailing comment on the anchor line" in text
    assert "# a stray column-zero comment inside the block" in text
    assert "# keep me" in text
    raw = yaml.safe_load(text)
    assert raw["brand"]["mark"] == {
        "asset_type": "logo",
        "file": "the mark.png",
        "kind": "generated",
    }
    # Neighboring values are untouched, including the block scalar.
    assert raw["brand"]["tagline"] == "Real tagline."
    assert raw["brand"]["description"].startswith("Multi-line description")
    assert raw["brand"]["website"] == "https://example.com"


def test_never_overwrites_existing_mark(tmp_path: Path) -> None:
    marked = PLAIN_YAML.replace(
        "  parent: null\n",
        "  parent: null\n"
        "  mark:\n"
        "    asset_type: logo\n"
        "    file: chosen.png\n"
        "    kind: imported\n",
    )
    root = _kit(tmp_path, "markless", marked)
    before = (root / "markless" / "brand.yaml").read_text()
    ok = maybe_set_brand_mark("markless", "logo", "later.png", "imported", kits_dir=root)
    assert ok is False
    assert (root / "markless" / "brand.yaml").read_text() == before


def test_invalid_yaml_is_nonfatal(tmp_path: Path) -> None:
    root = _kit(tmp_path, "broken", "brand: [unclosed\n  nope::: {\n")
    before = (root / "broken" / "brand.yaml").read_text()
    ok = maybe_set_brand_mark("broken", "logo", "x.png", "imported", kits_dir=root)
    assert ok is False  # logged, swallowed, nothing written
    assert (root / "broken" / "brand.yaml").read_text() == before


def test_missing_brand_yaml_is_nonfatal(tmp_path: Path) -> None:
    ok = maybe_set_brand_mark("ghost", "logo", "x.png", "imported", kits_dir=tmp_path)
    assert ok is False


def test_falls_back_to_slug_line_when_no_display_name(tmp_path: Path) -> None:
    no_display = (
        "brand:\n  slug: bare\nvisual:\n  colors:\n"
        '    primary: "#112233"\n    accent: "#445566"\n'
    )
    root = _kit(tmp_path, "bare", no_display)
    ok = maybe_set_brand_mark("bare", "logo", "m.png", "imported", kits_dir=root)
    assert ok is True
    raw = yaml.safe_load((root / "bare" / "brand.yaml").read_text())
    assert raw["brand"]["mark"]["file"] == "m.png"


# ─────────────────────────── Approve path: _apply_transition ────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    return TestClient(create_app())


@pytest.fixture
def cleanup_real_kits() -> Iterator[list[str]]:
    created: list[str] = []
    try:
        yield created
    finally:
        for slug in created:
            shutil.rmtree(DEFAULT_KITS_DIR / slug, ignore_errors=True)


def _plant_generated(slug: str, asset_type: str, file_name: str) -> None:
    """Write a generated asset + sidecar the transition route can move."""
    gen_dir = DEFAULT_KITS_DIR / slug / "generated" / asset_type
    gen_dir.mkdir(parents=True, exist_ok=True)
    (gen_dir / file_name).write_bytes(_TINY_PNG)
    sidecar = {
        "brand": slug,
        "asset_type": asset_type,
        "kind": "generated",
        "variant": "flat",
        "backend": "test",
        "model": None,
        "mime": "image/png",
        "size": [1, 1],
        "transparent": True,
        "cost_cents": 0.0,
        "created_at": "2026-07-11-00-00-00",
        "source": "generated",
        "prompt": None,
        "reference_images": [],
        "lifecycle_status": "needs_review",
    }
    (gen_dir / f"{file_name}.json").write_text(json.dumps(sidecar, indent=2))


def test_approving_generated_logo_sets_mark(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    slug = "nb-approve-mark"
    cleanup_real_kits.append(slug)
    created = mcp_server.new_brand(slug, "NB Approve", parent="magic-unicorn")
    assert "error" not in created
    _plant_generated(slug, "logo", "candidate.png")

    resp = client.post(
        "/api/assets/approve",
        json={"brand": slug, "asset": "logo", "file": "candidate.png", "kind": "generated"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset"]["lifecycle_status"] == "approved"

    # The approved generated logo became the canonical mark, kind preserved
    # (approval moves no files, so the mark names the generated tree).
    raw = yaml.safe_load((DEFAULT_KITS_DIR / slug / "brand.yaml").read_text())
    assert raw["brand"]["mark"] == {
        "asset_type": "logo",
        "file": "candidate.png",
        "kind": "generated",
    }

    # And the brand list now serves a mark_url for it, out of the generated tree.
    brands = client.get("/api/brands").json()["brands"]
    mine = next(b for b in brands if b["slug"] == slug)
    assert mine["mark_url"] is not None
    assert "kind=generated" in mine["mark_url"]
    assert "file=candidate.png" in mine["mark_url"]


def test_approving_non_logo_does_not_mark(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    slug = "nb-approve-og"
    cleanup_real_kits.append(slug)
    created = mcp_server.new_brand(slug, "NB Approve OG", parent="magic-unicorn")
    assert "error" not in created
    _plant_generated(slug, "og_image", "share.png")

    resp = client.post(
        "/api/assets/approve",
        json={"brand": slug, "asset": "og_image", "file": "share.png", "kind": "generated"},
    )
    assert resp.status_code == 200, resp.text
    raw = yaml.safe_load((DEFAULT_KITS_DIR / slug / "brand.yaml").read_text())
    assert "mark" not in raw["brand"]


def test_approving_second_logo_keeps_first_mark(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    slug = "nb-approve-second"
    cleanup_real_kits.append(slug)
    created = mcp_server.new_brand(slug, "NB Approve Second", parent="magic-unicorn")
    assert "error" not in created
    _plant_generated(slug, "logo", "first.png")
    _plant_generated(slug, "logo", "second.png")

    first = client.post(
        "/api/assets/approve",
        json={"brand": slug, "asset": "logo", "file": "first.png", "kind": "generated"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/assets/approve",
        json={"brand": slug, "asset": "logo", "file": "second.png", "kind": "generated"},
    )
    assert second.status_code == 200

    raw = yaml.safe_load((DEFAULT_KITS_DIR / slug / "brand.yaml").read_text())
    assert raw["brand"]["mark"]["file"] == "first.png"
