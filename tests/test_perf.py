"""Performance-shaped correctness tests for the enterprise-hardening pass.

These assert the OPTIMIZATIONS preserve behavior, not raw timings:

* ``load_brand`` memoizes the parsed Brand keyed on the brand.yaml path +
  ``st_mtime_ns``, so a repeat read of an unchanged file does not re-parse, and
  an edit (new mtime) invalidates the cache naturally.
* the brand-wide compliance rollup computes from each asset's STORED compliance
  stamp and does not re-decode the image when a stamp is present.

All synthesized in a ``tmp_path`` kit; no network, no model, no repo files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from brand_ops import brand_loader
from brand_ops.brand_loader import clear_brand_cache, load_brand

_BRAND = "perf-test-brand"


def _write_brand(kits_dir: Path, slug: str = _BRAND, *, parent: str | None = None) -> Path:
    """Write a minimal valid brand.yaml and return its path."""
    brand_dir = kits_dir / slug
    brand_dir.mkdir(parents=True, exist_ok=True)
    brand_block: dict = {"slug": slug, "display_name": "Perf Test"}
    if parent:
        brand_block["parent"] = parent
    manifest = {
        "brand": brand_block,
        "visual": {
            "colors": {
                "primary": "#b66eff",
                "accent": "#00d4ff",
                "background": "#0a0a0a",
                "foreground": "#ffffff",
            }
        },
        "voice": {"forbidden_phrases": ["revolutionary"]},
    }
    path = brand_dir / "brand.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return path


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Each test starts with an empty brand cache so identity assertions are clean."""
    clear_brand_cache()
    yield
    clear_brand_cache()


# ─────────────────────────── load_brand cache ───────────────────────────────


def test_load_brand_caches_until_mtime_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeat load of an unchanged brand.yaml returns the cached object, no re-read.

    The second call must hit the cache (same object identity, and the YAML read
    counter does not advance). Touching the file with a new mtime invalidates the
    entry, so the next call re-reads and re-parses (a fresh object).
    """
    _write_brand(tmp_path)

    reads = {"n": 0}
    real_read = brand_loader._read_yaml

    def _counting_read(slug: str, kits_dir: Path) -> dict:
        reads["n"] += 1
        return real_read(slug, kits_dir)

    monkeypatch.setattr(brand_loader, "_read_yaml", _counting_read)

    first = load_brand(_BRAND, tmp_path)
    assert reads["n"] == 1, "the first load parses from disk"

    second = load_brand(_BRAND, tmp_path)
    assert second is first, "an unchanged brand is served from the cache"
    assert reads["n"] == 1, "a cache hit does not re-read the file"

    # A new mtime (an edit) must invalidate the entry. Bump mtime explicitly so
    # the test does not depend on filesystem timestamp granularity.
    path = tmp_path / _BRAND / "brand.yaml"
    new_ns = path.stat().st_mtime_ns + 1_000_000_000
    import os

    os.utime(path, ns=(new_ns, new_ns))

    third = load_brand(_BRAND, tmp_path)
    assert reads["n"] == 2, "a changed mtime forces a re-read"
    assert third is not first, "an edited brand yields a freshly parsed object"


def test_load_brand_cache_keys_on_path_not_slug(tmp_path: Path) -> None:
    """Two roots holding the same slug never collide: each caches its own parse.

    The cache key is the resolved brand.yaml path (plus its mtime), not the slug,
    so a second workspace's same-named brand cannot return the first's object.
    """
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    _write_brand(root_a)
    # Same slug, different display copy, under a different root.
    brand_dir = root_b / _BRAND
    brand_dir.mkdir(parents=True, exist_ok=True)
    (brand_dir / "brand.yaml").write_text(
        yaml.safe_dump(
            {
                "brand": {"slug": _BRAND, "display_name": "Other Root"},
                "visual": {
                    "colors": {
                        "primary": "#b66eff",
                        "accent": "#00d4ff",
                        "background": "#0a0a0a",
                        "foreground": "#ffffff",
                    }
                },
            }
        )
    )

    a = load_brand(_BRAND, root_a)
    b = load_brand(_BRAND, root_b)
    assert a is not b
    assert a.brand.display_name == "Perf Test"
    assert b.brand.display_name == "Other Root"
    # Each is independently cached and stable.
    assert load_brand(_BRAND, root_a) is a
    assert load_brand(_BRAND, root_b) is b


def test_load_brand_subbrand_invalidates_on_parent_edit(tmp_path: Path) -> None:
    """A sub-brand re-parses when its PARENT file changes, not only its own.

    The merged result depends on both files, so the cache folds the parent's
    path+mtime into the key. Editing the parent must invalidate the child.
    """
    _write_brand(tmp_path, "perf-parent")
    # The child overrides only its display name and inherits the parent's whole
    # visual block (the merge replaces a section like ``visual`` wholesale unless
    # the child supplies its own), so a parent edit to ``accent`` flows through
    # the merge and proves the re-parse actually ran (not just a new object).
    child_dir = tmp_path / "perf-child"
    child_dir.mkdir(parents=True, exist_ok=True)
    (child_dir / "brand.yaml").write_text(
        yaml.safe_dump(
            {
                "brand": {
                    "slug": "perf-child",
                    "display_name": "Perf Child",
                    "parent": "perf-parent",
                }
            }
        )
    )

    child = load_brand("perf-child", tmp_path)
    assert child.visual.colors.accent == "#00d4ff"  # inherited from the parent
    assert load_brand("perf-child", tmp_path) is child, "stable while both unchanged"

    # Edit the parent's accent (and bump mtime); the child's merged parse must
    # refresh and pick the new inherited value up.
    parent_path = tmp_path / "perf-parent" / "brand.yaml"
    data = yaml.safe_load(parent_path.read_text())
    data["visual"]["colors"]["accent"] = "#123456"
    parent_path.write_text(yaml.safe_dump(data))
    new_ns = parent_path.stat().st_mtime_ns + 1_000_000_000
    import os

    os.utime(parent_path, ns=(new_ns, new_ns))

    refreshed = load_brand("perf-child", tmp_path)
    assert refreshed is not child, "a parent edit invalidates the sub-brand"
    assert refreshed.visual.colors.accent == "#123456"


# ─────────────────────── rollup uses stored stamps ──────────────────────────


def _place_stamped_asset(
    kits_dir: Path,
    file_name: str,
    *,
    status: str,
    score: int,
    violations: list[str] | None = None,
) -> None:
    """Drop a sidecar carrying a compliance stamp (and a NON-decodable image).

    The image bytes are deliberately not a real PNG: if the rollup tried to
    re-decode it the color check would run (and, here, skip), proving by the
    asserted status/score that the rollup used the STORED stamp instead.
    """
    asset_dir = kits_dir / _BRAND / "generated" / "logo"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / file_name
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real image
    record = {
        "brand": _BRAND,
        "asset_type": "logo",
        "created_at": "2026-01-01-00-00-00",
        "reference_images": [],
        "lifecycle_status": "approved" if status != "fail" else "needs_review",
        "compliance": {
            "status": status,
            "score": score,
            "violations": violations or [],
        },
    }
    image_path.with_suffix(image_path.suffix + ".json").write_text(
        json.dumps(record, indent=2)
    )


def test_rollup_uses_stored_stamps_without_redecoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The brand rollup reads each asset's stamp and never calls check_asset.

    Every catalog asset carries a compliance stamp, so the rollup must compute
    purely from those (no re-check). We assert check_asset is never invoked and
    that the summary reflects the stamped verdicts exactly.
    """
    from brand_ops.api import routes
    from brand_ops.brand_loader import reset_workspace_scope, set_workspace_scope

    _write_brand(tmp_path)
    _place_stamped_asset(tmp_path, "pass.png", status="pass", score=100)
    _place_stamped_asset(
        tmp_path, "fail.png", status="fail", score=40, violations=["[fail] x"]
    )
    _place_stamped_asset(tmp_path, "vector.png", status="skipped", score=0)

    def _boom(*a: object, **k: object):
        raise AssertionError("check_asset must not run when every asset is stamped")

    monkeypatch.setattr(routes, "check_asset", _boom)

    # Point the readers at the synth kit by scoping to a workspace whose subtree
    # is tmp_path. compute_brand_compliance/read_assets resolve through
    # current_kits_dir(); set DEFAULT_KITS_DIR via the kits_dir override is not
    # exposed there, so drive it through the unscoped global root instead.
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", tmp_path)
    token = set_workspace_scope(None, None)
    try:
        rollup = routes.compute_brand_compliance(_BRAND)
    finally:
        reset_workspace_scope(token)

    summary = rollup.summary
    assert summary.total == 3
    assert summary.pass_ == 1
    assert summary.fail == 1
    assert summary.skipped == 1
    # Average excludes the skipped asset: (100 + 40) / 2 = 70.
    assert summary.score == 70
    # The stamped verdicts are surfaced per row.
    by_file = {row.file: row for row in rollup.assets}
    assert by_file["fail.png"].status == "fail"
    assert by_file["fail.png"].violations == ["[fail] x"]


def test_rollup_rechecks_only_unstamped_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An asset with NO stamp falls back to check_asset; stamped ones do not.

    Mixed catalog: one stamped, one unstamped. check_asset must run exactly once
    (for the unstamped asset) and never for the stamped one.
    """
    from brand_ops.api import routes
    from brand_ops.brand_loader import reset_workspace_scope, set_workspace_scope

    _write_brand(tmp_path)
    _place_stamped_asset(tmp_path, "stamped.png", status="pass", score=90)

    # An unstamped asset: a sidecar with no ``compliance`` key.
    asset_dir = tmp_path / _BRAND / "generated" / "logo"
    image_path = asset_dir / "unstamped.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    image_path.with_suffix(image_path.suffix + ".json").write_text(
        json.dumps(
            {
                "brand": _BRAND,
                "asset_type": "logo",
                "created_at": "2026-01-02-00-00-00",
                "reference_images": [],
            }
        )
    )

    checked: list[str] = []

    def _spy_check(slug, file, asset_type, kind, rules, *a, **k):
        checked.append(file)
        return {"status": "warn", "score": 80, "violations": ["[warn] y"]}

    monkeypatch.setattr(routes, "check_asset", _spy_check)
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", tmp_path)

    token = set_workspace_scope(None, None)
    try:
        rollup = routes.compute_brand_compliance(_BRAND)
    finally:
        reset_workspace_scope(token)

    assert checked == ["unstamped.png"], "only the unstamped asset is re-checked"
    by_file = {row.file: row for row in rollup.assets}
    assert by_file["stamped.png"].status == "pass"
    assert by_file["stamped.png"].score == 90
    assert by_file["unstamped.png"].status == "warn"
    assert by_file["unstamped.png"].score == 80
