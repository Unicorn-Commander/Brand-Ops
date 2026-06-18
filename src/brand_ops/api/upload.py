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
from pathlib import Path

from ..activity import append_event
from ..asset_types import ASSET_TYPES
from ..brand_loader import current_kits_dir
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


def _unique_destination(dest_dir: Path, file_name: str) -> Path:
    """A non-colliding path in ``dest_dir`` for ``file_name``.

    Imports are managed copies, so we never overwrite an existing file; a clash
    gets a ``-1``, ``-2`` suffix on the stem. Keeps the catalog from silently
    replacing one asset's bytes (and sidecar) with another's.
    """
    candidate = dest_dir / file_name
    if not candidate.exists():
        return candidate
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


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

    dest = _unique_destination(dest_dir, stored_name)
    dest.write_bytes(data)
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
    record = _build_record(sidecar, dest)
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
    sidecar.write_text(json.dumps(data, indent=2))


def _build_record(sidecar: Path, image_path: Path) -> AssetRecord | None:
    """Read the sidecar back into the same AssetRecord the catalog serves."""
    import json

    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):  # pragma: no cover - sidecar was just written
        return None
    return _coerce_record(data, sidecar, image_path, kind="imported")
