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
import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.api.upload import MAX_IMAGE_PIXELS, UploadError, _verify_raster_bytes
from brand_ops.brand_loader import DEFAULT_KITS_DIR

# A 1x1 transparent PNG, the smallest valid PNG. Decoded from base64 so the test
# carries no binary blob and needs no fixture file on disk.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# Pillow drives the decode-verification. It is the optional [backends] extra, so
# the verification tests skip cleanly when it is absent (the verifier itself
# degrades to a no-op there, which the dedicated absent-Pillow test asserts).
try:
    from PIL import Image as _PILImage

    _HAS_PILLOW = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _PILImage = None  # type: ignore[assignment]
    _HAS_PILLOW = False

_needs_pillow = pytest.mark.skipif(
    not _HAS_PILLOW, reason="Pillow (the [backends] extra) is required"
)


def _raster_bytes(
    fmt: str,
    size: tuple[int, int] = (4, 4),
    color: tuple[int, int, int] = (10, 20, 30),
) -> bytes:
    """Encode a small solid image as ``fmt`` (PNG/JPEG/WEBP) for the tests."""
    assert _PILImage is not None
    buf = io.BytesIO()
    _PILImage.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _bomb_png_bytes() -> bytes:
    """A real PNG whose dimensions exceed the pixel cap but whose bytes are tiny.

    A solid 9000x9000 image is 81 megapixels (over the 64 MP cap) yet compresses
    to a few hundred KB, well under the 25 MB byte limit: the decompression-bomb
    shape the byte cap alone cannot catch.
    """
    assert _PILImage is not None
    side = 9000
    assert side * side > MAX_IMAGE_PIXELS
    buf = io.BytesIO()
    _PILImage.new("RGB", (side, side), (10, 20, 30)).save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) < 25 * 1024 * 1024  # stays under the byte cap, as a bomb does
    return raw


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


def test_uploaded_svg_serves_as_forced_download(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    # SVG is in the upload allowlist, so a malicious one can be stored. The fix
    # is at serving: the bytes must come back as an attachment with nosniff, so
    # an embedded <script> never executes inline on a shared origin once auth is
    # on. This exercises the full upload -> serve loop end to end.
    brand = "magic-unicorn"
    asset_type = "logo"
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<script>alert(document.domain)</script></svg>'
    )
    resp = client.post(
        "/api/assets/upload",
        data={"brand": brand, "asset_type": asset_type},
        files={"file": ("xss.svg", svg, "image/svg+xml")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    cleanup_uploads.append(_imported_path(brand, asset_type, record["file"]))
    assert record["file"].endswith(".svg")

    served = client.get(record["url"])
    assert served.status_code == 200
    assert served.headers["content-disposition"].startswith("attachment")
    assert served.headers["x-content-type-options"] == "nosniff"


def test_uploaded_png_serves_inline(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    # The counterpart to the SVG case: an uploaded raster must keep serving
    # inline (no attachment) so the Library can show it in an <img>.
    brand = "magic-unicorn"
    asset_type = "logo"
    resp = client.post(
        "/api/assets/upload",
        data={"brand": brand, "asset_type": asset_type},
        files={"file": ("inline-raster.png", _TINY_PNG, "image/png")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    cleanup_uploads.append(_imported_path(brand, asset_type, record["file"]))

    served = client.get(record["url"])
    assert served.status_code == 200
    assert "content-disposition" not in served.headers
    assert served.headers["x-content-type-options"] == "nosniff"


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


# ─────────────────────── content verification (T3.2) ────────────────────────
# These confirm the upload decodes and validates the bytes, not just the declared
# content-type, so a renamed or junk file and a decompression bomb are refused,
# while a valid raster and an SVG still pass.


@_needs_pillow
def test_upload_rejects_decompression_bomb(client: TestClient) -> None:
    # A tiny-on-disk PNG that decodes to 81 MP (over the 64 MP cap). It clears the
    # byte cap, so only the decode-pixel check stops it: it must be a clean 422,
    # never a 500, and nothing is written.
    bomb = _bomb_png_bytes()
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("bomb.png", bomb, "image/png")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "image_too_large"


@_needs_pillow
def test_upload_rejects_mislabeled_png_as_jpg(client: TestClient) -> None:
    # Real PNG bytes presented under a .jpg name and image/jpeg type. The declared
    # type resolves the stored extension to .jpg, but the decoded format is PNG, so
    # the format-mismatch check rejects it.
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("renamed.jpg", _TINY_PNG, "image/jpeg")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "type_mismatch"


@_needs_pillow
def test_upload_rejects_junk_bytes_as_png(client: TestClient) -> None:
    # Bytes that are not an image at all, declared as a PNG. verify() cannot
    # identify them, so they are rejected as a corrupt image (422, not 500).
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("junk.png", b"this is definitely not a PNG", "image/png")},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "corrupt_image"


@_needs_pillow
def test_upload_accepts_real_webp(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    # A genuine WebP whose bytes match its declared type passes the decode check.
    webp = _raster_bytes("WEBP")
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("real.webp", webp, "image/webp")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    cleanup_uploads.append(_imported_path("magic-unicorn", "logo", record["file"]))
    assert record["file"].endswith(".webp")


def test_upload_svg_bypasses_raster_verification(
    client: TestClient, cleanup_uploads: list[Path]
) -> None:
    # SVG is vector, not Pillow-decodable: it must still upload (the raster decode
    # check is skipped for it). Runs with or without Pillow installed.
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'
    resp = client.post(
        "/api/assets/upload",
        data={"brand": "magic-unicorn", "asset_type": "logo"},
        files={"file": ("vector.svg", svg, "image/svg+xml")},
    )
    assert resp.status_code == 200, resp.text
    record = resp.json()["asset"]
    cleanup_uploads.append(_imported_path("magic-unicorn", "logo", record["file"]))
    assert record["file"].endswith(".svg")


# --- unit-level checks on the verifier itself --------------------------------


@_needs_pillow
def test_verify_raster_accepts_matching_formats() -> None:
    # The format the verifier requires per extension must match what Pillow reports
    # for an honest file of that type. A no-raise call means it accepted the bytes.
    _verify_raster_bytes(_raster_bytes("PNG"), ".png")
    _verify_raster_bytes(_raster_bytes("JPEG"), ".jpg")
    _verify_raster_bytes(_raster_bytes("WEBP"), ".webp")


@_needs_pillow
def test_verify_raster_rejects_oversized_pixels() -> None:
    # The decode-pixel cap is enforced from the decoded dimensions, raising the
    # mapped UploadError rather than a bare exception.
    with pytest.raises(UploadError) as excinfo:
        _verify_raster_bytes(_bomb_png_bytes(), ".png")
    assert excinfo.value.code == "image_too_large"


def test_verify_raster_skips_when_pillow_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    # With Pillow unavailable the verifier is a no-op (the prior content-type/size
    # checks still guarded the upload), so even junk bytes pass this layer. Force
    # the optional import to fail so the test holds whether or not Pillow is
    # installed in the environment.
    import builtins

    real_import = builtins.__import__

    def _no_pillow(name: str, *args: object, **kwargs: object):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("forced: Pillow unavailable")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_pillow)
    # No raise: the verifier returns early when the import fails.
    _verify_raster_bytes(b"not an image", ".png")
