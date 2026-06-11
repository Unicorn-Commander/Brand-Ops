"""Asset lifecycle gate tests.

The gate is the deterministic compliance loop applied to an asset's sidecar:
``stamp_compliance`` scores one asset and writes ``lifecycle_status`` +
a ``compliance`` summary into its sidecar (fail -> needs_review, otherwise
approved). These tests are network- and LLM-free:

* the stamp tests run fully inside a synthesized ``tmp_path`` kit (its own
  brand.yaml + small in-memory PIL images + hand-written sidecars), so they
  touch nothing in the repo;
* the endpoint tests (approve, status filter) exercise the in-process TestClient
  against the real ``brand-kits/magic-unicorn`` tree the routes read from, and so
  track and DELETE every file they create in a finally block, like test_upload.

Pillow drives the color check; the stamp assertions skip cleanly without it.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.brand_loader import DEFAULT_KITS_DIR
from brand_ops.compliance import stamp_compliance

try:  # the color check needs Pillow; skip the pixel-dependent tests without it
    from PIL import Image

    _HAS_PIL = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _HAS_PIL = False

needs_pil = pytest.mark.skipif(not _HAS_PIL, reason="Pillow not installed")

_BRAND = "lifecycle-test-brand"


# ─────────────────────────── synthesized kit ────────────────────────────────


def _write_brand(kits_dir: Path) -> None:
    """Write a minimal valid brand.yaml whose palette is the unicorn purple/cyan."""
    brand_dir = kits_dir / _BRAND
    brand_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "brand": {"slug": _BRAND, "display_name": "Lifecycle Test"},
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
    (brand_dir / "brand.yaml").write_text(yaml.safe_dump(manifest))


def _place_asset(
    kits_dir: Path,
    file_name: str,
    image: Image.Image,
    *,
    asset_type: str = "logo",
    kind: str = "generated",
    prompt: str | None = None,
) -> None:
    """Drop an image plus a catalog sidecar into the synthesized kit."""
    asset_dir = kits_dir / _BRAND / kind / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / file_name
    image.save(image_path)
    record = {
        "brand": _BRAND,
        "asset_type": asset_type,
        "variant": None,
        "backend": "test",
        "model": None,
        "mime": "image/png",
        "size": [image.width, image.height],
        "transparent": True,
        "cost_cents": 0.0,
        "created_at": "2026-01-01-00-00-00",
        "prompt": prompt,
        "reference_images": [],
    }
    image_path.with_suffix(image_path.suffix + ".json").write_text(
        json.dumps(record, indent=2)
    )


def _solid(rgb: tuple[int, int, int], size: int = 64) -> Image.Image:
    return Image.new("RGBA", (size, size), rgb + (255,))


def _half(
    left: tuple[int, int, int], right: tuple[int, int, int], size: int = 64
) -> Image.Image:
    img = Image.new("RGBA", (size, size), left + (255,))
    for x in range(size // 2, size):
        for y in range(size):
            img.putpixel((x, y), right + (255,))
    return img


def _read_sidecar(kits_dir: Path, file_name: str, asset_type: str = "logo") -> dict:
    sidecar = (
        kits_dir / _BRAND / "generated" / asset_type / f"{file_name}.json"
    )
    return json.loads(sidecar.read_text())


# ─────────────────────────── stamp_compliance ───────────────────────────────


@needs_pil
def test_stamp_on_brand_asset_is_approved(tmp_path: Path) -> None:
    """A solid brand-purple asset passes compliance, so it is approved."""
    _write_brand(tmp_path)
    _place_asset(tmp_path, "on-brand.png", _solid((182, 110, 255)))  # #b66eff

    summary = stamp_compliance(_BRAND, "logo", "on-brand.png", "generated", tmp_path)

    assert summary["lifecycle_status"] == "approved"
    assert summary["status"] in ("pass", "warn", "skipped")
    # The verdict is persisted into the sidecar, other keys preserved.
    data = _read_sidecar(tmp_path, "on-brand.png")
    assert data["lifecycle_status"] == "approved"
    assert data["compliance"]["status"] == summary["status"]
    assert data["compliance"]["score"] == summary["score"]
    assert data["brand"] == _BRAND  # untouched original key


@needs_pil
def test_stamp_off_brand_asset_needs_review(tmp_path: Path) -> None:
    """A half-red asset fails the color check, so it needs review."""
    _write_brand(tmp_path)
    _place_asset(tmp_path, "off-brand.png", _half((182, 110, 255), (255, 0, 0)))

    summary = stamp_compliance(_BRAND, "logo", "off-brand.png", "generated", tmp_path)

    assert summary["lifecycle_status"] == "needs_review"
    assert summary["status"] == "fail"
    assert summary["violations"]  # at least one violation line is recorded
    data = _read_sidecar(tmp_path, "off-brand.png")
    assert data["lifecycle_status"] == "needs_review"
    assert data["compliance"]["status"] == "fail"


@needs_pil
def test_stamp_banned_prompt_needs_review(tmp_path: Path) -> None:
    """An on-brand image with a banned word in its prompt still needs review."""
    _write_brand(tmp_path)
    _place_asset(
        tmp_path,
        "bad-copy.png",
        _solid((182, 110, 255)),
        prompt="a revolutionary new mark",
    )

    summary = stamp_compliance(_BRAND, "logo", "bad-copy.png", "generated", tmp_path)

    assert summary["lifecycle_status"] == "needs_review"
    assert any("banned_terms" in v for v in summary["violations"])


def test_stamp_missing_sidecar_degrades(tmp_path: Path) -> None:
    """A stamp against a nonexistent asset returns a null status, writes nothing."""
    _write_brand(tmp_path)
    summary = stamp_compliance(_BRAND, "logo", "ghost.png", "generated", tmp_path)
    assert summary == {"lifecycle_status": None}


# ─────────────── signal-only activity feed (flag, not clear) ─────────────────


@needs_pil
def test_stamp_off_brand_emits_one_flag_event(tmp_path: Path) -> None:
    """Flagging an asset off-brand records exactly one 'flagged off-brand' event.

    The feed (under the same tmp kits dir) carries the gate's signal so the
    operator sees what needs attention.
    """
    from brand_ops.activity import read_events

    _write_brand(tmp_path)
    _place_asset(tmp_path, "off-brand.png", _half((182, 110, 255), (255, 0, 0)))

    stamp_compliance(_BRAND, "logo", "off-brand.png", "generated", tmp_path)

    events = read_events(brand=_BRAND, kits_dir=tmp_path)
    assert len(events) == 1
    assert events[0]["action"] == "flagged off-brand"
    assert events[0]["actor"] == "compliance"
    assert events[0]["actor_kind"] == "system"
    assert events[0]["target"] == "logo/off-brand.png"


@needs_pil
def test_stamp_on_brand_emits_no_event(tmp_path: Path) -> None:
    """Clearing an on-brand asset (approved) writes NOTHING to the feed.

    A clear is a no-op for the operator; emitting it would flood the feed during
    a bulk backfill of mostly-on-brand assets. The feed is a signal, not a log.
    """
    from brand_ops.activity import read_events

    _write_brand(tmp_path)
    _place_asset(tmp_path, "on-brand.png", _solid((182, 110, 255)))  # #b66eff

    summary = stamp_compliance(_BRAND, "logo", "on-brand.png", "generated", tmp_path)

    assert summary["lifecycle_status"] == "approved"
    assert read_events(brand=_BRAND, kits_dir=tmp_path) == []


def test_backfill_does_not_flood_feed_with_clears(tmp_path: Path) -> None:
    """A backfill over only on-brand assets leaves the feed empty.

    The regression guard for the signal-only feed: the common mostly-on-brand
    bulk path must not produce a wall of 'cleared' no-ops. Pillow-independent —
    a synth kit with no real images yields skipped->approved verdicts, which are
    still clears and so still silent.
    """
    from brand_ops.activity import read_events
    from brand_ops.compliance.lifecycle import backfill_compliance

    _write_brand(tmp_path)
    # Three assets whose bytes are not decodable images: the color check skips,
    # the prompt is clean, so each resolves to approved (a clear) -> no event.
    for i in range(3):
        asset_dir = tmp_path / _BRAND / "generated" / "logo"
        asset_dir.mkdir(parents=True, exist_ok=True)
        image_path = asset_dir / f"clear-{i}.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
        record = {
            "brand": _BRAND,
            "asset_type": "logo",
            "created_at": "2026-01-01-00-00-00",
            "reference_images": [],
        }
        image_path.with_suffix(image_path.suffix + ".json").write_text(
            json.dumps(record, indent=2)
        )

    counts = backfill_compliance(kits_dir=tmp_path)

    assert counts["stamped"] == 3
    assert counts["approved"] == 3
    assert counts["needs_review"] == 0
    # The whole point: no clear events on the feed.
    assert read_events(brand=_BRAND, kits_dir=tmp_path) == []


# ─────────────────────────── REST surface ───────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


@pytest.fixture
def cleanup_assets() -> Iterator[list[Path]]:
    """Track files written into the real kit and remove each (plus sidecar)."""
    created: list[Path] = []
    try:
        yield created
    finally:
        for image in created:
            sidecar = image.with_suffix(image.suffix + ".json")
            for path in (sidecar, image):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def _seed_real_sidecar(
    created: list[Path],
    *,
    file_name: str,
    asset_type: str,
    lifecycle_status: str,
) -> Path:
    """Write a sidecar (and a tiny placeholder image) into the real kit tree."""
    asset_dir = DEFAULT_KITS_DIR / "magic-unicorn" / "generated" / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / file_name
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real PNG; never decoded here
    created.append(image_path)
    record = {
        "brand": "magic-unicorn",
        "asset_type": asset_type,
        "variant": None,
        "backend": "test",
        "mime": "image/png",
        "size": [1, 1],
        "created_at": "2026-01-01-00-00-00",
        "lifecycle_status": lifecycle_status,
        "compliance": {"status": "fail", "score": 50, "violations": ["x"]},
        "reference_images": [],
    }
    image_path.with_suffix(image_path.suffix + ".json").write_text(
        json.dumps(record, indent=2)
    )
    return image_path


def test_approve_flips_needs_review_to_approved(
    client: TestClient, cleanup_assets: list[Path]
) -> None:
    """POST /api/assets/approve overrides a needs_review asset to approved."""
    file_name = "test-lifecycle-approve.png"
    _seed_real_sidecar(
        cleanup_assets,
        file_name=file_name,
        asset_type="og_image",
        lifecycle_status="needs_review",
    )

    resp = client.post(
        "/api/assets/approve",
        json={
            "brand": "magic-unicorn",
            "file": file_name,
            "asset": "og_image",
            "kind": "generated",
        },
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    assert record["lifecycle_status"] == "approved"
    assert record["file"] == file_name
    # The override is persisted to the sidecar.
    sidecar = (
        DEFAULT_KITS_DIR
        / "magic-unicorn"
        / "generated"
        / "og_image"
        / f"{file_name}.json"
    )
    assert json.loads(sidecar.read_text())["lifecycle_status"] == "approved"


def test_approve_missing_sidecar_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/assets/approve",
        json={
            "brand": "magic-unicorn",
            "file": "does-not-exist.png",
            "asset": "og_image",
            "kind": "generated",
        },
    )
    assert resp.status_code == 404


def test_assets_status_filter(
    client: TestClient, cleanup_assets: list[Path]
) -> None:
    """The status filter on GET /api/assets returns only matching lifecycle rows."""
    approved_file = "test-lifecycle-filter-approved.png"
    review_file = "test-lifecycle-filter-review.png"
    _seed_real_sidecar(
        cleanup_assets,
        file_name=approved_file,
        asset_type="og_image",
        lifecycle_status="approved",
    )
    _seed_real_sidecar(
        cleanup_assets,
        file_name=review_file,
        asset_type="og_image",
        lifecycle_status="needs_review",
    )

    review = client.get(
        "/api/assets",
        params={"brand": "magic-unicorn", "asset": "og_image", "status": "needs_review"},
    )
    assert review.status_code == 200
    review_files = {a["file"] for a in review.json()["assets"]}
    assert review_file in review_files
    assert approved_file not in review_files

    approved = client.get(
        "/api/assets",
        params={"brand": "magic-unicorn", "asset": "og_image", "status": "approved"},
    )
    assert approved.status_code == 200
    approved_files = {a["file"] for a in approved.json()["assets"]}
    assert approved_file in approved_files
    assert review_file not in approved_files


def test_assets_invalid_status_is_422(client: TestClient) -> None:
    resp = client.get("/api/assets", params={"status": "bogus"})
    assert resp.status_code == 422
