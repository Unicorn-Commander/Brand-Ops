"""The DB read path and the sidecar read path must return the IDENTICAL record.

This is the parity guarantee behind the catalog source-of-truth transition: a
caller cannot tell whether ``/api/assets`` was served from Postgres or from the
on-disk sidecars. It pins ``_asset_to_record`` (DB row -> AssetRecord) against
``_coerce_record`` (sidecar dict -> AssetRecord) field for field, so a drift in
either path is caught here without needing a database.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from brand_ops.api.catalog import _asset_to_record, _coerce_record, read_assets


def test_db_row_and_sidecar_yield_the_same_record() -> None:
    sidecar = {
        "brand": "acme",
        "asset_type": "logo",
        "variant": "wordmark",
        "backend": "litellm",
        "model": "google/gemini-3.1-flash-image-preview",
        "mime": "image/png",
        "size": [512, 512],
        "transparent": False,
        "cost_cents": 0.42,
        "prompt": "a clean wordmark",
        "reference_images": ["imported/logo/ref.png"],
        "created_at": "2026-06-20-14-30-00",
        "lifecycle_status": "approved",
        "compliance": {"status": "pass", "score": 100, "violations": []},
    }
    image = Path("/kits/WS/acme/generated/logo/2026-06-20-14-30-00-logo.png")
    sidecar_path = image.with_suffix(image.suffix + ".json")
    from_file = _coerce_record(sidecar, sidecar_path, image, kind="generated")

    # The DB row carries the same facts, split the way the columns store them
    # (size -> width/height, created_at label -> created_label, storage_key the
    # workspace-relative path whose basename is the file name).
    row = SimpleNamespace(
        storage_key="acme/generated/logo/2026-06-20-14-30-00-logo.png",
        brand_slug="acme",
        asset_type="logo",
        kind="generated",
        variant="wordmark",
        backend="litellm",
        model="google/gemini-3.1-flash-image-preview",
        mime="image/png",
        width=512,
        height=512,
        transparent=False,
        cost_cents=0.42,
        prompt="a clean wordmark",
        reference_images=["imported/logo/ref.png"],
        lifecycle_status="approved",
        compliance={"status": "pass", "score": 100, "violations": []},
        created_label="2026-06-20-14-30-00",
    )
    from_db = _asset_to_record(row)

    assert from_db is not None
    # Byte-for-byte identical record: same fields, same file URL, same order keys.
    assert from_db.model_dump() == from_file.model_dump()


def test_read_assets_labels_brand_by_directory_not_sidecar_field(tmp_path: Path) -> None:
    # An imported asset whose sidecar records a DIFFERENT source brand than the kit
    # directory it physically lives in. The catalog must label it by the directory
    # (where the bytes are), so its /api/assets/file URL resolves; a field-based
    # label would point the URL at a brand kit that does not hold the file.
    asset_dir = tmp_path / "magic-unicorn" / "imported" / "logo"
    asset_dir.mkdir(parents=True)
    image = asset_dir / "uc1-unicorn-logo.svg"
    image.write_bytes(b"<svg/>")
    sidecar = image.with_suffix(image.suffix + ".json")
    sidecar.write_text(
        json.dumps(
            {
                "brand": "unicorn-commander",  # the SOURCE brand, not the directory
                "asset_type": "logo",
                "mime": "image/svg+xml",
                "created_at": "2026-06-19-00-00-00",
            }
        )
    )

    records = read_assets(brand="magic-unicorn", kits_dir=tmp_path)
    assert len(records) == 1
    assert records[0].brand == "magic-unicorn"  # directory wins over the field
    assert "brand=magic-unicorn" in records[0].url


def test_db_row_without_storage_key_is_skipped() -> None:
    # A pre-SoR row (no storage key) has no file to serve, so the DB path drops it
    # rather than emitting a record with a broken URL.
    row = SimpleNamespace(
        storage_key=None,
        brand_slug="acme",
        asset_type="logo",
        kind="generated",
        variant=None,
        backend=None,
        model=None,
        mime=None,
        width=None,
        height=None,
        transparent=None,
        cost_cents=None,
        prompt=None,
        reference_images=None,
        lifecycle_status=None,
        compliance=None,
        created_label=None,
    )
    assert _asset_to_record(row) is None
