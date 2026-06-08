"""Asset-upload endpoint tests.

These exercise POST /api/assets/upload with the in-process TestClient and no
network: auth is off by default (AUTH_REQUIRED unset), so every request resolves
to the anonymous principal and no token or Keycloak is needed. No image API and
no LLM are touched; the endpoint only writes bytes and a catalog sidecar to disk.

The happy-path test writes into the real ``brand-kits/<brand>/imported/`` tree
(the same tree GET /api/assets reads), so it tracks and DELETES every file and
sidecar it creates in a finally block, leaving the repo as it found it.
"""
from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.brand_loader import DEFAULT_KITS_DIR

# A 1x1 transparent PNG, the smallest valid PNG. Decoded from base64 so the test
# carries no binary blob and needs no fixture file on disk.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Make auth-off explicit and deterministic regardless of the ambient env.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


@pytest.fixture
def cleanup_uploads() -> Iterator[list[Path]]:
    """Track uploaded files and remove each (plus its sidecar) after the test."""
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


def _imported_path(brand: str, asset_type: str, file_name: str) -> Path:
    return DEFAULT_KITS_DIR / brand / "imported" / asset_type / file_name


def test_upload_png_returns_record_and_appears_in_catalog(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    brand = "magic-unicorn"
    asset_type = "logo"
    upload_name = "test-upload-unit.png"

    resp = client.post(
        "/api/assets/upload",
        data={"brand": brand, "asset_type": asset_type},
        files={"file": (upload_name, _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "asset" in body
    record = body["asset"]

    # Track the file for cleanup before any further assertion can fail.
    stored_file = record["file"]
    created_path = _imported_path(brand, asset_type, stored_file)
    cleanup_uploads.append(created_path)
    assert created_path.is_file()

    # Record shape: the same AssetRecord GET /api/assets returns for an import.
    assert record["brand"] == brand
    assert record["asset_type"] == asset_type
    assert record["kind"] == "imported"
    assert record["mime"] == "image/png"
    assert stored_file.endswith(".png")
    # The URL routes back through /api/assets/file with kind=imported.
    assert record["url"] is not None
    assert record["url"].startswith("/api/assets/file?")
    assert "kind=imported" in record["url"]
    assert f"file={stored_file}" in record["url"]

    # GET /api/assets (brand-filtered) now includes the uploaded asset.
    listing = client.get("/api/assets", params={"brand": brand})
    assert listing.status_code == 200
    files = {a["file"] for a in listing.json()["assets"]}
    assert stored_file in files

    # And the bytes serve back through the file route.
    served = client.get(record["url"])
    assert served.status_code == 200
    assert served.content == _TINY_PNG


def test_upload_with_variant_is_recorded(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    brand = "magic-unicorn"
    asset_type = "logo"
    resp = client.post(
        "/api/assets/upload",
        data={"brand": brand, "asset_type": asset_type, "variant": "dark"},
        files={"file": ("test-upload-variant.png", _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    cleanup_uploads.append(_imported_path(brand, asset_type, record["file"]))
    assert record["variant"] == "dark"


def test_upload_unknown_brand_is_404(client: TestClient) -> None:
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "no-such-brand", "asset_type": "logo"},
        files={"file": ("x.png", _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 404


def test_upload_unknown_asset_type_is_422(client: TestClient) -> None:
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "not-a-type"},
        files={"file": ("x.png", _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_asset_type"


def test_upload_rejects_non_image_type(client: TestClient) -> None:
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("notes.txt", b"plain text, not an image", "text/plain")},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["error"] == "unsupported_type"


def test_upload_sanitizes_traversal_filename(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    brand = "magic-unicorn"
    asset_type = "logo"
    # A traversal-laden name must be reduced to a safe basename inside the kit.
    resp = client.post(
        "/api/assets/upload",
        data={"brand": brand, "asset_type": asset_type},
        files={"file": ("../../../etc/evil.png", _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    stored = record["file"]
    cleanup_uploads.append(_imported_path(brand, asset_type, stored))
    # No path separators or traversal survive into the stored name.
    assert "/" not in stored
    assert "\\" not in stored
    assert ".." not in stored
    assert stored == "evil.png"
    # The file is confined to the brand kit's imported tree.
    created = _imported_path(brand, asset_type, stored).resolve()
    assert created.is_relative_to((DEFAULT_KITS_DIR / brand).resolve())
