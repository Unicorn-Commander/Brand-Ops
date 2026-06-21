"""File handling for the asset upload endpoint.

The Library lets a user drop an existing image into a brand kit. This module is
the pure, network-free core of that: it validates the bytes and the declared
type, sanitizes the filename, writes the file into
``brand-kits/<brand>/imported/<asset_type>/`` next to the assets ``ingest.py``
produces, and writes the same catalog sidecar via ``ingest._write_sidecar`` so
the upload is indistinguishable from an imported asset in the Library. The HTTP
route in ``routes.py`` is a thin adapter over ``save_upload`` here.

It calls no provider SDK and reads no secret. Validation failures raise
``UploadError`` with a stable ``code`` and a clean message, which the route maps
onto an HTTP 422; nothing here raises a 500 for ordinary bad input.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from ..activity import append_event
from ..asset_types import ASSET_TYPES
from ..atomic_write import atomic_write_bytes, atomic_write_text, reserve_unique_path
from ..brand_loader import current_kits_dir, get_current_workspace_id
from ..catalog_index import index_sidecar_sync
from ..compliance.lifecycle import stamp_compliance
from ..ingest import _write_sidecar
from .catalog import _coerce_record
from .schemas import AssetRecord

# The image MIME types we accept, mapped to the canonical extension we store the
# file under. This is the allowlist: a content type outside it is rejected, and
# the stored suffix is derived from the type, not from the (untrusted) uploaded
# name, so a mislabeled extension cannot smuggle in a different kind of file.
ACCEPTED_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}

# Some clients send SVG as text/* or a vendor type, and some send a bare octet
# stream and rely on the extension. We accept those only when the filename's
# extension is itself an accepted image extension, resolving to that extension.
_EXT_FALLBACK: dict[str, str] = {
    ".png": ".png",
    ".jpg": ".jpg",
    ".jpeg": ".jpg",
    ".svg": ".svg",
    ".webp": ".webp",
}
_AMBIGUOUS_MIME = frozenset(
    {"", "application/octet-stream", "image/svg", "text/plain", "text/xml", "text/html"}
)

# Cap the upload at ~25 MB. Large enough for a high-resolution PNG or podcast
# cover, small enough to refuse an accidental video or archive.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# Cap the DECODED pixel count, independent of the byte cap. A decompression bomb
# stays tiny on disk (well under the 25 MB byte limit) yet expands to billions of
# pixels on decode, which would exhaust memory. 64 megapixels (~8000x8000) clears
# any legitimate brand asset (an 8K image is ~33 MP) while refusing a bomb. This
# is stricter than Pillow's own ~89 MP default DOS guard.
MAX_IMAGE_PIXELS = 64 * 1024 * 1024

# The stored extensions Pillow can decode as raster images, mapped to the exact
# Pillow format string an honest file of that type must report. A .svg is vector,
# not Pillow-decodable, so it is intentionally absent and bypasses the decode
# check. .jpeg never reaches storage (normalized to .jpg) so it is not listed.
_RASTER_EXT_TO_PIL_FORMAT: dict[str, str] = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".webp": "WEBP",
}


@dataclass(frozen=True)
class UploadError(Exception):
    """A rejected upload. ``code`` is a stable machine token; ``message`` is for
    humans. The route turns this into an HTTP 422.
    """

    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


def sanitize_filename(name: str) -> str:
    """Reduce an uploaded filename to a safe basename, no path or traversal.

    Strips any directory component (forward or backslash), drops a leading dot so
    the result cannot be a dotfile, and collapses to the trailing path segment.
    Returns an empty string when nothing usable remains, which the caller treats
    as a missing name and rejects.
    """
    # Normalize both separators, then take the final segment. This neutralizes
    # ``../``, absolute paths, and Windows ``..\\`` alike.
    flattened = name.replace("\\", "/")
    base = flattened.rsplit("/", 1)[-1].strip()
    # Refuse the dot entries outright; strip any leading dots from a real name.
    if base in ("", ".", ".."):
        return ""
    base = base.lstrip(".")
    return base.strip()


def _resolve_extension(content_type: str, safe_name: str) -> str | None:
    """Pick the stored extension from the content type, or the name as a fallback.

    A recognized image content type wins. For an ambiguous type (octet-stream, a
    text/* SVG), fall back to the filename's own extension only if that extension
    is itself an accepted image extension. Returns None when neither resolves,
    which the caller surfaces as an unsupported-type rejection.
    """
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype in ACCEPTED_MIME_EXT:
        return ACCEPTED_MIME_EXT[ctype]
    if ctype in _AMBIGUOUS_MIME:
        suffix = Path(safe_name).suffix.lower()
        return _EXT_FALLBACK.get(suffix)
    return None


def _verify_raster_bytes(data: bytes, ext: str) -> None:
    """Decode-verify that ``data`` is a real image whose format matches ``ext``.

    Runs only for the raster extensions in ``_RASTER_EXT_TO_PIL_FORMAT`` (SVG and
    any other non-raster type bypass this, since Pillow cannot decode a vector).
    Three checks, in order:

    1. The bytes parse as an image of a recognized format, and that format matches
       the format the declared extension requires. A renamed file (PNG bytes sent
       as ``.jpg``) or junk fails the format match or fails to open at all.
    2. The header pixel count is under ``MAX_IMAGE_PIXELS``. Checked from the
       dimensions *before* decoding, so a decompression bomb (tiny on disk,
       enormous decoded) is rejected without ever allocating the full raster, even
       though it slips under the byte cap.
    3. The pixels actually decode (``load()``). This catches a truncated or
       corrupt body that opens but cannot be read.

    Structural strictness is deliberately left to ``load()`` rather than
    ``verify()``: ``verify()`` fails on a bad chunk CRC, which many real,
    browser-renderable PNGs carry and which the rest of this app already serves,
    so using it would reject valid assets. ``load()`` proves the bytes are
    decodable, which is the property that matters here.

    Pillow is optional (the ``[backends]`` extra). When it is absent this is a
    no-op and the upload falls back to the content-type/size/name checks that
    guarded it before, matching how the rest of the suite treats Pillow.

    Raises ``UploadError`` (mapped to HTTP 422 by the route) on any failure, never
    a bare exception that would surface as a 500.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        # Pillow not installed: keep the prior behavior (content-type + size +
        # filename checks already ran). No decode verification available.
        return

    expected_format = _RASTER_EXT_TO_PIL_FORMAT.get(ext)
    if expected_format is None:  # pragma: no cover - guarded by the caller
        return

    # Tighten the decode cap to the app limit so a clearly oversized image raises
    # in Pillow itself; the dimension check below closes the gap between the cap
    # and Pillow's own warn-only 2x band.
    previous_cap = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(BytesIO(data)) as img:
            actual_format = (img.format or "").upper()
            if actual_format != expected_format:
                raise UploadError(
                    "type_mismatch",
                    f"file content is {actual_format or 'an unrecognized format'}, "
                    f"which does not match the declared {expected_format} type",
                )
            # Enforce the pixel cap from the header dimensions before decoding, so
            # a bomb is refused without materializing its raster.
            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadError(
                    "image_too_large",
                    "the image decodes to too many pixels and was rejected as a "
                    "decompression bomb",
                )
            # Decode the pixels to confirm the body is real (not truncated).
            img.load()
    except UploadError:
        raise
    except Image.DecompressionBombError as exc:
        raise UploadError(
            "image_too_large",
            "the image decodes to too many pixels and was rejected as a "
            "decompression bomb",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise UploadError(
            "corrupt_image",
            "the file is not a valid image of the declared type",
        ) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = previous_cap


def _mime_for_ext(ext: str) -> str:
    """The canonical image MIME for a stored extension."""
    for mime, mapped in ACCEPTED_MIME_EXT.items():
        if mapped == ext:
            return mime
    # ``.jpeg`` never reaches storage (we normalize to .jpg), so this is total.
    return "application/octet-stream"  # pragma: no cover - defensive


def _ensure_extension(safe_name: str, ext: str) -> str:
    """Guarantee the stored name carries the resolved extension exactly once."""
    stem = Path(safe_name).stem
    if not stem:
        # The name was all suffix (e.g. ".png"); give it a stable stem.
        stem = "upload"
    return f"{stem}{ext}"


def save_upload(
    *,
    brand: str,
    asset_type: str,
    filename: str,
    content_type: str,
    data: bytes,
    variant: str | None = None,
    kits_dir: Path | None = None,
) -> AssetRecord:
    """Validate and persist one uploaded image, returning its catalog record.

    Raises ``UploadError`` for any bad input (unknown asset type, unsupported or
    oversized file, unusable filename). The brand's existence is the route's job
    to check before calling this, since loading a brand is the route's concern;
    this layer owns the file mechanics. On success it writes the bytes and a
    sidecar and returns the same ``AssetRecord`` shape ``/api/assets`` serves for
    an imported asset, including its ``/api/assets/file`` URL.
    """
    if asset_type not in ASSET_TYPES:
        known = ", ".join(sorted(ASSET_TYPES))
        raise UploadError(
            "unknown_asset_type",
            f"unknown asset type {asset_type!r}. Known types: {known}",
        )

    if not data:
        raise UploadError("empty_file", "the uploaded file is empty")
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadError(
            "file_too_large",
            f"file is larger than the {mb} MB limit",
        )

    safe_name = sanitize_filename(filename or "")
    if not safe_name:
        raise UploadError("invalid_filename", "the uploaded filename is not usable")

    ext = _resolve_extension(content_type, safe_name)
    if ext is None:
        accepted = ", ".join(sorted(ACCEPTED_MIME_EXT))
        raise UploadError(
            "unsupported_type",
            f"unsupported image type. Accepted content types: {accepted}",
        )

    # Decode-verify the bytes for raster types: the content-type and extension are
    # declarations the client controls, so confirm the bytes are a real image of
    # that format (rejecting a renamed file or junk) and are not a decompression
    # bomb, before anything is written. SVG and any non-raster type bypass this.
    _verify_raster_bytes(data, ext)

    stored_name = _ensure_extension(safe_name, ext)

    root = kits_dir or current_kits_dir()
    dest_dir = (root / brand / "imported" / asset_type).resolve()
    # Defense in depth: the destination must stay inside this brand's kit even
    # after a sanitized name. brand and asset_type are validated by the caller
    # and the registry, so this guards only against a future regression.
    kit_root = (root / brand).resolve()
    if not dest_dir.is_relative_to(kit_root):
        raise UploadError("invalid_path", "resolved upload path escapes the brand kit")
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Reserve a non-colliding destination atomically (O_EXCL), so a second upload
    # whose sanitized name matches an existing asset lands on a -N name instead of
    # overwriting it -- and two concurrent uploads can't both claim the same name.
    dest = reserve_unique_path(dest_dir, stored_name)
    atomic_write_bytes(dest, data)
    # Reuse the ingest sidecar writer so an upload looks identical to an import in
    # the catalog. source="upload" distinguishes it from a disk-sourced import.
    _write_sidecar(dest, brand, asset_type, source="upload")

    # Stamp the variant the user chose, if any. The shared sidecar writer always
    # records variant=None, so patch it in rather than fork the writer.
    if variant:
        _set_sidecar_variant(dest, variant)

    # Gate the asset at creation time: stamp its lifecycle status into the
    # sidecar from the deterministic compliance verdict, so an uploaded asset is
    # reviewed the moment it lands. Best-effort — a stamping failure must not
    # fail an upload that has already written its bytes and sidecar.
    try:
        stamp_compliance(brand, asset_type, dest.name, "imported", root)
    except Exception:  # noqa: BLE001 - stamping is advisory, never fatal
        pass

    sidecar = dest.with_suffix(dest.suffix + ".json")
    # Dual-write the catalog row into Postgres from the now-stamped sidecar, the
    # same SoR transition the generation path does. Best-effort and a no-op when
    # unscoped or without a DATABASE_URL; reads stay on the sidecars until the SoR
    # flag flips. After the stamp so the row carries the lifecycle verdict.
    index_sidecar_sync(
        get_current_workspace_id(),
        sidecar_path=sidecar,
        workspace_root=root,
        kind="imported",
    )
    record = _build_record(sidecar, dest, brand)
    if record is None:  # pragma: no cover - sidecar was just written
        raise UploadError("write_failed", "the upload was saved but could not be read back")

    # Record the upload on the provenance feed: a human dropped this asset in.
    # Best-effort and after the record is built, so a feed failure never fails an
    # upload whose bytes and sidecar are already written.
    try:
        append_event(
            "you",
            "human",
            "uploaded",
            brand=brand,
            target=f"{asset_type}/{dest.name}",
            ts=datetime.now(UTC).isoformat(),
            kits_dir=root,
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the upload
        pass

    return record


def _set_sidecar_variant(dest: Path, variant: str) -> None:
    """Patch the just-written sidecar to carry the chosen variant."""
    import json

    sidecar = dest.with_suffix(dest.suffix + ".json")
    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):  # pragma: no cover - sidecar was just written
        return
    data["variant"] = variant
    atomic_write_text(sidecar, json.dumps(data, indent=2))


def _build_record(
    sidecar: Path, image_path: Path, brand: str | None = None
) -> AssetRecord | None:
    """Read the sidecar back into the same AssetRecord the catalog serves.

    ``brand`` is the owning brand (the destination kit directory), passed so the
    record + its file URL key on where the bytes physically are -- the same
    directory-authoritative rule the catalog walk uses.
    """
    import json

    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):  # pragma: no cover - sidecar was just written
        return None
    return _coerce_record(data, sidecar, image_path, kind="imported", brand=brand)
