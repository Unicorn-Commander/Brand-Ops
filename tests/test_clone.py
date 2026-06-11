"""Tests for clone-a-brand: the helper, the REST route, and the MCP tool.

The helper is exercised against a throwaway copy of the repo's brand-kits (a
tmp ``kits_dir``), so the real kit set is never written. The REST and MCP layers
go through ``DEFAULT_KITS_DIR`` (they take no kits_dir), so those tests register
a cleanup that removes any brand directory they create — the repo brand-kits is
left exactly as it was found.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from brand_ops import mcp_server
from brand_ops.api.app import create_app
from brand_ops.brand_clone import clone_brand
from brand_ops.brand_loader import DEFAULT_KITS_DIR, BrandNotFound, load_brand

# The source brand every clone test seeds from, and its known parent primary.
SOURCE = "magic-unicorn"
SOURCE_PRIMARY = "#b66eff"


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    """A throwaway copy of the repo brand-kits the helper writes into.

    Cloning into this tree never touches the real brand-kits, so the helper-level
    tests are fully isolated from the repo.
    """
    dest = tmp_path / "brand-kits"
    shutil.copytree(DEFAULT_KITS_DIR, dest)
    return dest


@pytest.fixture
def cleanup_real_kits() -> Iterator[list[str]]:
    """Track brand slugs created in the *real* brand-kits and remove them after.

    The REST and MCP surfaces resolve against DEFAULT_KITS_DIR, so a clone there
    lands in the repo. A test appends each new slug to the yielded list; the
    fixture deletes those directories on teardown so no stray test brand remains.
    """
    created: list[str] = []
    try:
        yield created
    finally:
        for slug in created:
            shutil.rmtree(DEFAULT_KITS_DIR / slug, ignore_errors=True)


# ─────────────────────────── Helper: clone_brand ────────────────────────────


def test_clone_as_child_inherits_parent_visual(kits: Path) -> None:
    brand = clone_brand(
        SOURCE,
        "mu-child",
        "MU Child",
        as_child=True,
        kits_dir=kits,
    )
    # Inheritance works: the child carries no colors of its own in the file, yet
    # the loaded brand resolves the parent's primary through the merge.
    assert brand.brand.parent == SOURCE
    assert brand.brand.display_name == "MU Child"
    assert brand.visual.colors.primary == SOURCE_PRIMARY

    # The written file is genuinely minimal: a brand block only, with no visual,
    # voice, or prompts of its own (so the merge is doing the work). The block
    # now also carries the owning ``org`` -- the default ``magic-unicorn`` here,
    # since this helper-level test runs unscoped (no ``current_org`` set).
    raw = yaml.safe_load((kits / "mu-child" / "brand.yaml").read_text())
    assert set(raw) == {"brand"}
    assert raw["brand"] == {
        "slug": "mu-child",
        "display_name": "MU Child",
        "parent": SOURCE,
        "org": "magic-unicorn",
    }
    # A clone never copies the source mark.
    assert brand.brand.mark is None


def test_clone_distinct_copies_colors_with_no_parent(kits: Path) -> None:
    brand = clone_brand(
        SOURCE,
        "mu-distinct",
        "MU Distinct",
        as_child=False,
        kits_dir=kits,
    )
    # A distinct brand: parent is null but the source colors are copied in.
    assert brand.brand.parent is None
    assert brand.visual.colors.primary == SOURCE_PRIMARY

    raw = yaml.safe_load((kits / "mu-distinct" / "brand.yaml").read_text())
    assert raw["brand"]["parent"] is None
    # The colors live in the file itself now, not inherited.
    assert raw["visual"]["colors"]["primary"] == SOURCE_PRIMARY
    # The mark is never copied, even for a distinct clone.
    assert "mark" not in raw["brand"]
    assert brand.brand.mark is None


def test_clone_distinct_diverges_from_source(kits: Path) -> None:
    # A distinct clone is decoupled: editing the source after the clone must not
    # change the clone (proving the visual was copied, not inherited).
    clone_brand(SOURCE, "mu-frozen", "MU Frozen", as_child=False, kits_dir=kits)

    src_path = kits / SOURCE / "brand.yaml"
    src_raw = yaml.safe_load(src_path.read_text())
    src_raw["visual"]["colors"]["primary"] = "#123456"
    src_path.write_text(yaml.safe_dump(src_raw, sort_keys=False))

    clone = load_brand("mu-frozen", kits_dir=kits)
    assert clone.visual.colors.primary == SOURCE_PRIMARY


def test_clone_duplicate_slug_raises(kits: Path) -> None:
    clone_brand(SOURCE, "mu-dup", "MU Dup", kits_dir=kits)
    with pytest.raises(FileExistsError):
        clone_brand(SOURCE, "mu-dup", "MU Dup Again", kits_dir=kits)


def test_clone_bad_slug_raises(kits: Path) -> None:
    with pytest.raises(ValueError):
        clone_brand(SOURCE, "Bad Slug!", "Bad", kits_dir=kits)
    # Nothing should have been written for the rejected slug.
    assert not (kits / "Bad Slug!").exists()


def test_clone_unknown_source_raises(kits: Path) -> None:
    with pytest.raises(BrandNotFound):
        clone_brand("no-such-brand", "mu-orphan", "Orphan", kits_dir=kits)
    assert not (kits / "mu-orphan").exists()


def test_clone_bad_slug_does_not_resolve_source(kits: Path) -> None:
    # A bad new_slug is rejected before the source is even read, so even an
    # unknown source with a bad slug surfaces the slug error first.
    with pytest.raises(ValueError):
        clone_brand("no-such-brand", "Bad Slug!", "Bad", kits_dir=kits)


# ─────────────────────────── REST: /api/brands/{slug}/clone ─────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def test_route_clone_as_child_returns_branddetail(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    cleanup_real_kits.append("mu-child-api")
    resp = client.post(
        f"/api/brands/{SOURCE}/clone",
        json={"new_slug": "mu-child-api", "display_name": "MU Child API"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Built exactly like GET /api/brands/{slug}: full BrandDetail shape.
    assert body["slug"] == "mu-child-api"
    assert body["display_name"] == "MU Child API"
    assert body["parent"] == SOURCE
    # Inherited visual through the merge.
    assert body["visual"]["colors"]["primary"] == SOURCE_PRIMARY
    assert isinstance(body["prompt_assets"], list)
    assert isinstance(body["reference_roles"], list)
    # The clone has no logo yet: mark_url is null.
    assert body["mark_url"] is None

    # And it is now a real, fetchable brand at the same shape.
    follow = client.get("/api/brands/mu-child-api")
    assert follow.status_code == 200
    assert follow.json()["parent"] == SOURCE


def test_route_clone_distinct_parent_null(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    cleanup_real_kits.append("mu-distinct-api")
    resp = client.post(
        f"/api/brands/{SOURCE}/clone",
        json={
            "new_slug": "mu-distinct-api",
            "display_name": "MU Distinct API",
            "as_child": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["parent"] is None
    assert body["visual"]["colors"]["primary"] == SOURCE_PRIMARY
    assert body["mark_url"] is None


def test_route_clone_duplicate_is_409(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    cleanup_real_kits.append("mu-dup-api")
    first = client.post(
        f"/api/brands/{SOURCE}/clone",
        json={"new_slug": "mu-dup-api", "display_name": "Dup"},
    )
    assert first.status_code == 200
    again = client.post(
        f"/api/brands/{SOURCE}/clone",
        json={"new_slug": "mu-dup-api", "display_name": "Dup 2"},
    )
    assert again.status_code == 409


def test_route_clone_bad_slug_is_422(client: TestClient) -> None:
    resp = client.post(
        f"/api/brands/{SOURCE}/clone",
        json={"new_slug": "Bad Slug!", "display_name": "Bad"},
    )
    assert resp.status_code == 422
    # The rejected slug must not have created a directory.
    assert not (DEFAULT_KITS_DIR / "Bad Slug!").exists()


def test_route_clone_unknown_source_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/brands/no-such-brand/clone",
        json={"new_slug": "mu-orphan-api", "display_name": "Orphan"},
    )
    assert resp.status_code == 404
    assert not (DEFAULT_KITS_DIR / "mu-orphan-api").exists()


# ─────────────────────────── MCP: clone_brand tool ──────────────────────────


def test_mcp_clone_brand_inherits(cleanup_real_kits: list[str]) -> None:
    cleanup_real_kits.append("mu-child-mcp")
    result = mcp_server.clone_brand(SOURCE, "mu-child-mcp", "MU Child MCP")
    assert "error" not in result
    assert result["slug"] == "mu-child-mcp"
    assert result["parent"] == SOURCE
    # _brand_identity flattens colors; the inherited primary is present.
    assert result["colors"]["primary"] == SOURCE_PRIMARY


def test_mcp_clone_brand_duplicate_returns_error(
    cleanup_real_kits: list[str],
) -> None:
    cleanup_real_kits.append("mu-dup-mcp")
    first = mcp_server.clone_brand(SOURCE, "mu-dup-mcp", "Dup")
    assert "error" not in first
    again = mcp_server.clone_brand(SOURCE, "mu-dup-mcp", "Dup 2")
    assert "error" in again


def test_mcp_clone_brand_bad_slug_returns_error() -> None:
    result = mcp_server.clone_brand(SOURCE, "Bad Slug!", "Bad")
    assert "error" in result
    assert not (DEFAULT_KITS_DIR / "Bad Slug!").exists()
