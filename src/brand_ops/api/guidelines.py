"""Assemble a brand's full guidelines document (the brand book) from data.

``build_guidelines`` gathers the pieces Brand-Ops already projects — the brand
identity, the design tokens (``compliance.tokens.build_tokens``), the rules
manifest (``compliance.rules.build_rules``), the voice, the canonical mark URL,
the logo lockups, and every catalog asset grouped by type — into one dict
matching the ``BrandGuidelines`` contract. It runs no new heavy logic: the only
I/O is reading the same on-disk catalog sidecars ``read_assets`` reads, so the
result is deterministic for a given disk state. ``generated_at`` is omitted here
(the route leaves it null; nothing calls a clock).

Both the REST route and the MCP tool call this one builder, so the human path
and the agent path return the identical brand book.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from ..asset_types import ASSET_TYPES
from ..brand_loader import load_brand
from ..compliance import build_rules, build_tokens
from ..models import Brand
from .catalog import read_assets


def _mark_url(brand: Brand) -> str | None:
    """Build the /api/assets/file URL for the brand's canonical mark, or None.

    Mirrors ``api.routes._mark_url`` so the mark resolves identically whichever
    surface assembled it; the file component is percent-encoded because mark
    filenames can carry spaces or other reserved characters.
    """
    mark = brand.brand.mark
    if mark is None:
        return None
    return (
        f"/api/assets/file?brand={brand.brand.slug}"
        f"&asset={mark.asset_type}"
        f"&file={quote(mark.file)}"
        f"&kind={mark.kind}"
    )


def build_guidelines(slug: str, kits_dir: Path | None = None) -> dict:
    """Assemble the brand book for ``slug`` as a ``BrandGuidelines``-shaped dict.

    Loads the brand (raising ``BrandNotFound`` for an unknown slug, the caller's
    to map to 404), then projects tokens and rules, gathers voice and the mark
    URL, lists the logo lockups, and groups every catalog asset by type in the
    asset-type registry order with the registry label and a count. Pure beyond
    the catalog disk read. ``generated_at`` is left out so the route can null it.
    """
    brand = load_brand(slug, kits_dir=kits_dir)
    meta = brand.brand

    tokens = build_tokens(brand)
    rules = build_rules(brand)
    mark_url = _mark_url(brand)

    # The logo section: the canonical mark plus every asset filed as a logo.
    lockups = read_assets(brand=slug, asset="logo", kits_dir=kits_dir)

    # Every asset of the brand, read once, then bucketed by type. Grouping in
    # memory keeps this to a single catalog walk rather than one walk per type.
    all_assets = read_assets(brand=slug, kits_dir=kits_dir)
    by_type: dict[str, list] = {}
    for record in all_assets:
        by_type.setdefault(record.asset_type, []).append(record)

    # Walk the registry in its declared order so the brand book sections are
    # stable; include a type only when the brand actually has assets of it. The
    # label is the registry's human label.
    asset_groups: list[dict] = []
    for key, spec in ASSET_TYPES.items():
        items = by_type.get(key)
        if not items:
            continue
        asset_groups.append(
            {
                "asset_type": key,
                "label": spec.label,
                "count": len(items),
                "items": items,
            }
        )

    return {
        "brand": {
            "slug": meta.slug,
            "display_name": meta.display_name,
            "tagline": meta.tagline,
            "description": meta.description,
            "parent": meta.parent,
            "mark_url": mark_url,
        },
        "colors": tokens["color"],
        "typography": tokens["typography"],
        "rules": rules,
        "voice": {
            "tone": list(brand.voice.tone),
            "forbidden_phrases": list(brand.voice.forbidden_phrases),
        },
        "logo": {
            "mark_url": mark_url,
            "lockups": lockups,
        },
        "asset_groups": asset_groups,
    }
