"""Load and validate brand.yaml, with sub-brand inheritance.

A brand with `parent: <slug>` inherits the parent's visual and voice defaults
and its prompts, then overlays its own. The database import (a later
workstream) and the future service backend both call load_brand, so there is
one code path from file or DB to a validated Brand.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import Brand

DEFAULT_KITS_DIR = Path(__file__).resolve().parents[2] / "brand-kits"


class BrandNotFound(Exception):
    pass


def list_brands(kits_dir: Path | None = None) -> list[str]:
    root = kits_dir or DEFAULT_KITS_DIR
    if not root.exists():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "brand.yaml").exists()
    )


def _read_yaml(slug: str, kits_dir: Path) -> dict:
    path = kits_dir / slug / "brand.yaml"
    if not path.exists():
        available = ", ".join(list_brands(kits_dir)) or "(none)"
        raise BrandNotFound(f"brand {slug!r} not found. Available: {available}")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _merge(parent: dict, child: dict) -> dict:
    """Overlay child over parent. Visual and voice merge section by section so a
    sub-brand can override just a few colors; everything else replaces wholesale.
    """
    merged = dict(parent)
    for key, value in child.items():
        mergeable = key in ("visual", "voice")
        if mergeable and isinstance(value, dict) and isinstance(merged.get(key), dict):
            section = dict(merged[key])
            section.update(value)
            merged[key] = section
        else:
            merged[key] = value
    return merged


def load_brand(slug: str, kits_dir: Path | None = None) -> Brand:
    root = kits_dir or DEFAULT_KITS_DIR
    raw = _read_yaml(slug, root)
    parent_slug = (raw.get("brand") or {}).get("parent")
    if parent_slug:
        parent_raw = _read_yaml(parent_slug, root)
        raw = _merge(parent_raw, raw)
    return Brand.model_validate(raw)


def resolve_reference_images(
    slug: str, brand: Brand, kits_dir: Path | None = None
) -> tuple[dict[str, Path], list[str]]:
    """Return (existing refs by role, missing role names).

    A missing reference is a warning, not an error: composition mode is optional.
    """
    root = kits_dir or DEFAULT_KITS_DIR
    base = root / slug
    found: dict[str, Path] = {}
    missing: list[str] = []
    for role, ref in brand.reference_images.items():
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = (base / ref).resolve()
        if candidate.exists():
            found[role] = candidate
        else:
            missing.append(role)
    return found, missing
