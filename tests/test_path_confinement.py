"""Cross-tenant path-confinement: a caller-supplied asset/file/mark segment must
never escape the workspace subtree (the reader/compliance/mark hole the enterprise
audit found). The byte-serving routes had the guard; these prove the readers and
the mark resolver now share it via brand_loader.safe_subpath.
"""
from __future__ import annotations

import json
import types

import pytest

from brand_ops import engine
from brand_ops.api.catalog import read_assets
from brand_ops.brand_loader import (
    reset_workspace_scope,
    safe_subpath,
    set_workspace_scope,
)
from brand_ops.compliance.evaluator import _resolve_image_path


def _seed_asset(root, brand, asset_type, file, prompt="secret"):
    d = root / brand / "imported" / asset_type
    d.mkdir(parents=True, exist_ok=True)
    (d / file).write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / (file + ".json")).write_text(
        json.dumps({"brand": brand, "asset_type": asset_type, "prompt": prompt})
    )


# ─────────────────────────── safe_subpath unit ──────────────────────────────


def test_safe_subpath_allows_normal_segments(tmp_path):
    p = safe_subpath(tmp_path, "mine", "imported", "logo", "a.png")
    assert p is not None and p == (tmp_path / "mine/imported/logo/a.png").resolve()


@pytest.mark.parametrize(
    "parts",
    [
        ("..",),
        ("mine", ".."),
        ("mine", "../../etc"),
        ("a/b",),
        ("a\\b",),
        ("/etc/passwd",),
        ("mine", "imported", "../../../victim/imported/logo"),
        ("foo\x00bar",),
        ("",),
    ],
)
def test_safe_subpath_rejects_unsafe(tmp_path, parts):
    assert safe_subpath(tmp_path, *parts) is None


def test_safe_subpath_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    # 'link' is a valid single segment, but it resolves outside root -> rejected.
    assert safe_subpath(tmp_path, "link", "x.png") is None


# ─────────────────────── catalog reader (the leak vector) ───────────────────


def test_read_assets_rejects_cross_tenant_traversal(tmp_path):
    _seed_asset(tmp_path, "mine", "logo", "mine.png", prompt="mine-ok")
    _seed_asset(tmp_path, "victim", "logo", "secret.png", prompt="VICTIM-SECRET")
    token = set_workspace_scope("ws", frozenset({"mine"}))
    try:
        # normal read of the caller's own brand works
        ok = read_assets(brand="mine", asset="logo", kits_dir=tmp_path)
        assert [r.file for r in ok] == ["mine.png"]
        # a crafted asset segment that would climb into the sibling 'victim'
        # brand returns nothing and never reveals the victim's prompt
        for evil in ("..", "../victim/imported/logo", "../../victim/imported/logo"):
            leaked = read_assets(brand="mine", asset=evil, kits_dir=tmp_path)
            assert leaked == []
            assert all("VICTIM" not in (r.prompt or "") for r in leaked)
        # the victim brand is not even in the allow-list
        assert read_assets(brand="victim", asset="logo", kits_dir=tmp_path) == []
    finally:
        reset_workspace_scope(token)


# ─────────────────────── compliance resolver + mark ─────────────────────────


def test_resolve_image_path_confines(tmp_path):
    _seed_asset(tmp_path, "mine", "logo", "mine.png")
    rp = _resolve_image_path
    ok = rp("mine", "mine.png", "logo", "imported", tmp_path)
    assert ok is not None and ok.is_relative_to(tmp_path.resolve())
    # traversal in any caller segment -> None (no cross-tenant read/stamp)
    assert rp("mine", "secret.png", "../victim/imported/logo", "imported", tmp_path) is None
    assert rp("mine", "../../victim/imported/logo/secret.png", "logo", "imported", tmp_path) is None


def test_resolve_mark_rejects_absolute_and_traversal(tmp_path):
    _seed_asset(tmp_path, "mine", "logo", "mine.png")
    _seed_asset(tmp_path, "victim", "logo", "secret.png")
    brand = types.SimpleNamespace(
        brand=types.SimpleNamespace(
            mark=types.SimpleNamespace(file="mine.png", kind="imported", asset_type="logo")
        )
    )
    # an absolute mark must NOT be honored; falls back to the brand's own declared mark
    got = engine._resolve_mark("mine", brand, "/etc/hostname", tmp_path)
    assert got == (tmp_path / "mine/imported/logo/mine.png")
    assert str(got) != "/etc/hostname"
    # a traversing mark name cannot reach the victim's file
    got2 = engine._resolve_mark("mine", brand, "../../victim/imported/logo/secret.png", tmp_path)
    assert got2 == (tmp_path / "mine/imported/logo/mine.png")
    assert "victim" not in str(got2)
