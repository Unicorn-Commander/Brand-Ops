"""Ingest existing brand assets into the managed catalog.

Copies a brand's real on-disk assets (logos, icons, wordmarks) into
``brand-kits/<slug>/imported/<asset_type>/`` and writes a catalog sidecar beside
each, so they appear in the Library and serve through /api/assets/file exactly
like generated assets. Known per-brand source locations are below; the GUI
upload endpoint covers anything not on disk here (avatars and the like).

The imported/ tree is gitignored: these are managed copies, not the canonical
source, which stays in its original location and in the brand.yaml references.
"""
from __future__ import annotations

import glob
import json
import shutil
from datetime import datetime
from pathlib import Path

from .atomic_write import atomic_write_text
from .brand_loader import DEFAULT_KITS_DIR, current_kits_dir

_HOME = str(Path.home())
_DEV = "/Volumes/Studio Storage/Development"
_UCI = f"{_DEV}/UC-1/Unicorn-Commander-Themes/unicorn-icons/apps"
_MBK = f"{_DEV}/majiks-brand-kit/logos"
_CDP = f"{_DEV}/Center-Deep-Pro/keycloak-theme/center-deep/login/resources/img"
# Contact-Ops authors its canonical mark as vector in the kit's own source/ dir
# (tracked); ingest mirrors it into imported/ (managed copies) like the rest.
_CO_SRC = str(DEFAULT_KITS_DIR / "contact-ops" / "source")

# Repo root, resolved the same way brand_loader.DEFAULT_KITS_DIR is (this module
# lives at src/brand_ops/ingest.py, so parents[2] is the repo). A brand that is
# native to this suite keeps its canonical marks tracked inside its own kit at
# brand-kits/<slug>/source/, rather than in an external tree, so a fresh clone
# can reproduce it end to end: git clone -> ingest_brand(slug) copies the marks
# into imported/. The source/ folder is NOT gitignored (only imported/ and
# generated/ are), so the marks travel with the repo.
_REPO = str(Path(__file__).resolve().parents[2])
_POS = f"{_REPO}/brand-kits/project-ops/source"
_LOS = f"{_REPO}/brand-kits/listing-ops/source"

# Per brand: (path or glob, asset_type). Missing paths are skipped quietly.
IMPORT_SOURCES: dict[str, list[tuple[str, str]]] = {
    # Project-Ops is native to the suite: its marks live tracked in-repo (see
    # _REPO above), so this entry reproduces from a clone. projectops-mascot.png
    # is the brand mark declared in brand.yaml -> imported/logo/.
    "project-ops": [
        (f"{_POS}/projectops-mascot.png", "logo"),
        (f"{_POS}/the-colonel.png", "logo"),
        (f"{_POS}/projectops-crew-light.png", "logo"),
        (f"{_POS}/projectops-crew-dark.png", "logo"),
    ],
    # Listing-Ops is native to the suite: its marks live tracked in-repo under
    # source/ (see _LOS). The brand mark reuses the suite badge with an orbital
    # ring carrying a coin; mascot is the Sutler.
    "listing-ops": [
        (f"{_LOS}/listing-ops-mark.svg", "logo"),
        (f"{_LOS}/listing-ops-mark.png", "logo"),
        (f"{_LOS}/listing-ops-app-icon.png", "app_icon"),
    ],
    "magic-unicorn": [
        (f"{_HOME}/Pictures/magic_unicorn_logo.png", "logo"),
        (f"{_HOME}/Pictures/unicorn_subdued_black_logo.png", "logo"),
        (f"{_HOME}/AI-Software/MagiCode_sync/public/magic-unicorn-logo.svg", "logo"),
        (f"{_HOME}/Draw Anything output/*.png", "logo"),
    ],
    "unicorn-commander": [
        (f"{_UCI}/unicorn-commander-logo.svg", "logo"),
        (f"{_UCI}/unicorn-colonel.svg", "logo"),
    ],
    "majiks": [
        (f"{_MBK}/app-icon/png/*.png", "app_icon"),
        (f"{_MBK}/wordmarks/light/*.svg", "logo"),
        (f"{_MBK}/wordmarks/light/*.png", "logo"),
    ],
    "majiks-studio": [
        (f"{_DEV}/majiks.online/public/images/majiks-studio-*.png", "logo"),
    ],
    "center-deep": [
        (f"{_CDP}/center-deep-logo.png", "logo"),
    ],
    "contact-ops": [
        (f"{_CO_SRC}/contact-ops-mark.svg", "logo"),
        (f"{_CO_SRC}/contact-ops-mark.png", "logo"),
        (f"{_CO_SRC}/contact-ops-wordmark.svg", "logo"),
        (f"{_CO_SRC}/contact-ops-wordmark.png", "logo"),
        (f"{_CO_SRC}/contact-ops-mono.svg", "logo"),
        (f"{_CO_SRC}/contact-ops-app-icon.svg", "app_icon"),
        (f"{_CO_SRC}/contact-ops-app-icon.png", "app_icon"),
        (f"{_CO_SRC}/contact-ops-og.svg", "og_image"),
        (f"{_CO_SRC}/contact-ops-og.png", "og_image"),
    ],
}

_MIME = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def _mime_for(suffix: str) -> str:
    return _MIME.get(suffix.lower(), "application/octet-stream")


def _dims(path: Path) -> list[int]:
    """Best-effort pixel dimensions. Empty for vectors or if pillow is absent."""
    if path.suffix.lower() == ".svg":
        return []
    try:
        from PIL import Image
    except ImportError:
        return []
    try:
        with Image.open(path) as im:
            return [im.width, im.height]
    except Exception:
        return []


def _write_sidecar(
    dest: Path, slug: str, asset_type: str, source: str, now: datetime | None = None
) -> None:
    suffix = dest.suffix.lower()
    record = {
        "brand": slug,
        "asset_type": asset_type,
        "kind": "imported",
        "variant": None,
        "backend": "imported",
        "model": None,
        "mime": _mime_for(suffix),
        "size": _dims(dest),
        "transparent": suffix in (".png", ".svg"),
        "cost_cents": 0.0,
        "created_at": (now or datetime.now()).strftime("%Y-%m-%d-%H-%M-%S"),
        "source": source,
        "prompt": None,
        "reference_images": [],
    }
    atomic_write_text(dest.with_suffix(dest.suffix + ".json"), json.dumps(record, indent=2))


def ingest_brand(slug: str, kits_dir: Path | None = None) -> list[Path]:
    """Copy a brand's known assets into imported/ and write catalog sidecars.

    Idempotent: a destination that already exists is left as is. Returns the list
    of imported file paths (both new and already present).
    """
    root = kits_dir or current_kits_dir()
    imported: list[Path] = []
    for pattern, asset_type in IMPORT_SOURCES.get(slug, []):
        for match in sorted(glob.glob(pattern)):
            src = Path(match)
            if not src.is_file():
                continue
            dest_dir = root / slug / "imported" / asset_type
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
                _write_sidecar(dest, slug, asset_type, source=str(src))
            imported.append(dest)
    return imported


_KNOWN_TYPES = {"logo", "app_icon", "og_image", "podcast_cover", "social_card", "avatar"}


def ingest_paths(slug: str, items: list[dict], kits_dir: Path | None = None) -> list[Path]:
    """Ingest an explicit list of discovered assets into imported/.

    Each item is a dict with ``path`` (a file on disk), an optional
    ``asset_type`` (defaults to and is coerced to a known type), and an optional
    ``source`` for the sidecar. Used to catalog assets a discovery pass found on
    the marketing sites and press kits. Idempotent by destination filename.
    """
    root = kits_dir or current_kits_dir()
    imported: list[Path] = []
    for item in items:
        src = Path(str(item.get("path", "")))
        if not src.is_file():
            continue
        asset_type = item.get("asset_type") or "logo"
        if asset_type not in _KNOWN_TYPES:
            asset_type = "logo"
        dest_dir = root / slug / "imported" / asset_type
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
            _write_sidecar(dest, slug, asset_type, source=str(item.get("source", src)))
        imported.append(dest)
    return imported
