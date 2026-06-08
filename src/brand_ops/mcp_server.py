#!/usr/bin/env python3
"""Brand-Ops MCP server.

Exposes the Brand-Ops engine and brand loader as MCP tools so any MCP client
(Claude Desktop, Cursor, Claude Code, the Brigade autonomy agents) can list
brands, read a brand identity, compose a prompt, generate an asset, browse the
asset catalog, and bootstrap a new brand, all over native tool calls.

Every tool is a thin call into the engine or the loader. The engine is the one
entry point the CLI and the REST API also use, so the agent path and the human
path can never drift. Tools never raise across the MCP boundary: each failure is
caught and returned as a structured ``{"error": ...}`` dict so the client gets a
useful payload instead of a transport fault.

Transport: stdio (suitable for ``claude mcp add`` and equivalents).

Requires the ``mcp`` Python package (already a project dependency).
"""
from __future__ import annotations

import json
import shutil
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import engine
from .asset_types import ASSET_TYPES
from .backends.base import BackendError, BackendNotConfigured
from .brand_loader import (
    DEFAULT_KITS_DIR,
    BrandNotFound,
    load_brand,
    resolve_reference_images,
)
from .brand_loader import (
    list_brands as _list_brands,
)
from .models import Brand

mcp = FastMCP("brand-ops")

# Exceptions the engine and loader raise for caller mistakes. We translate each
# into a structured error rather than letting it cross the MCP boundary.
_USER_ERRORS = (BrandNotFound, KeyError, ValueError)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    """Build a structured error payload. Never raise across the boundary."""
    out: dict[str, Any] = {"error": str(message)}
    out.update(extra)
    return out


def _brand_identity(slug: str, brand: Brand) -> dict[str, Any]:
    """Project a loaded Brand into a flat, JSON-able identity record."""
    colors = brand.visual.colors
    typography = brand.visual.typography
    refs, missing = resolve_reference_images(slug, brand)
    return {
        "slug": brand.slug,
        "display_name": brand.brand.display_name,
        "tagline": brand.brand.tagline,
        "parent": brand.brand.parent,
        "description": (brand.brand.description or "").strip() or None,
        "colors": {
            "primary": colors.primary,
            "accent": colors.accent,
            "background": colors.background,
            "foreground": colors.foreground,
            "muted": colors.muted,
        },
        "fonts": {
            "primary_font": typography.primary_font,
            "display_font": typography.display_font,
            "mono_font": typography.mono_font,
        },
        "style_keywords": list(brand.visual.style_keywords),
        "voice": {
            "tone": list(brand.voice.tone),
            "forbidden_phrases": list(brand.voice.forbidden_phrases),
        },
        "references": {
            "declared": dict(brand.reference_images),
            "resolved": {role: str(path) for role, path in refs.items()},
            "missing": list(missing),
        },
    }


# ─────────────────────────── Read tools ─────────────────────────────────────


@mcp.tool()
def list_brands() -> dict[str, Any]:
    """List the brand slugs seeded under brand-kits/.

    Returns a dict with a ``brands`` list of slugs and a ``count``.
    """
    try:
        slugs = _list_brands()
        return {"brands": slugs, "count": len(slugs)}
    except Exception as exc:  # noqa: BLE001 - boundary guard, never raise
        return _error(exc)


@mcp.tool()
def get_brand(slug: str) -> dict[str, Any]:
    """Get one brand's identity: colors, fonts, voice, tagline, and references.

    Args:
        slug: Brand slug, for example "magic-unicorn".
    """
    try:
        brand = load_brand(slug)
        return _brand_identity(slug, brand)
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)


@mcp.tool()
def list_asset_types() -> dict[str, Any]:
    """List the asset-type registry.

    Each entry has key, label, size (width, height in pixels), default_backend,
    and transparent. These are the asset types build_prompt and generate_asset
    accept.
    """
    try:
        types = [
            {
                "key": spec.key,
                "label": spec.label,
                "size": list(spec.size),
                "default_backend": spec.default_backend,
                "transparent": spec.transparent,
            }
            for spec in ASSET_TYPES.values()
        ]
        return {"asset_types": types, "count": len(types)}
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc)


@mcp.tool()
def build_prompt(
    brand: str,
    asset: str,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Compose the image prompt for a brand asset without generating anything.

    This is a dry run through the same engine that live generation uses, so the
    prompt you review here is exactly what would be sent to the backend. No
    backend is called and no spend occurs.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        asset: Asset type key, for example "og_image". See list_asset_types.
        variant: Optional variant name allowed by the asset's prompt template.
        topic: Optional topic, used by text-heavy assets like og_image.
        platform: Optional platform hint, used by social_card.
        backend: Optional backend override (gemini, openai, recraft, flux).

    Returns the composed prompt text, the planned output path, the chosen
    backend, the output size, whether the output is transparent, any prompt
    warnings, and any missing reference roles.
    """
    try:
        outcome = engine.generate_asset(
            brand,
            asset,
            variant=variant,
            topic=topic,
            platform=platform,
            backend=backend,
            dry_run=True,
        )
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, asset=asset)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, asset=asset)

    built = outcome.built
    return {
        "brand": brand,
        "asset": asset,
        "variant": variant,
        "prompt": built.text,
        "backend": built.backend,
        "size": list(built.size),
        "transparent": built.transparent,
        "planned_output": str(outcome.planned_path),
        "reference_images": [str(p) for p in built.reference_images],
        "warnings": list(built.warnings),
        "missing_references": list(outcome.missing_refs),
    }


@mcp.tool()
def list_generated_assets(
    brand: str | None = None,
    asset: str | None = None,
) -> dict[str, Any]:
    """List previously generated assets from the on-disk catalog.

    Reads the sidecar JSON records written next to each generated image under
    brand-kits/<slug>/generated/<asset>/. Each record captures how the asset was
    made: prompt, backend, model, size, cost, and references.

    Args:
        brand: Optional brand slug to restrict the listing to one brand.
        asset: Optional asset-type key to restrict the listing to one type.

    Returns a list of records, each including the image path it describes.
    """
    try:
        root = DEFAULT_KITS_DIR
        if not root.exists():
            return {"assets": [], "count": 0}

        if brand:
            brand_dirs = [root / brand] if (root / brand).is_dir() else []
        else:
            brand_dirs = [
                p
                for p in root.iterdir()
                if p.is_dir() and not p.name.startswith("_")
            ]

        records: list[dict[str, Any]] = []
        for brand_dir in sorted(brand_dirs):
            gen_root = brand_dir / "generated"
            if not gen_root.is_dir():
                continue
            if asset:
                asset_dirs = [gen_root / asset] if (gen_root / asset).is_dir() else []
            else:
                asset_dirs = [p for p in gen_root.iterdir() if p.is_dir()]
            for asset_dir in sorted(asset_dirs):
                for sidecar in sorted(asset_dir.glob("*.json")):
                    try:
                        record = json.loads(sidecar.read_text())
                    except (OSError, ValueError) as exc:
                        records.append(
                            _error(exc, sidecar=str(sidecar))
                        )
                        continue
                    # The image the sidecar describes is the sidecar minus .json.
                    record["image_path"] = str(sidecar.with_suffix(""))
                    record["sidecar_path"] = str(sidecar)
                    records.append(record)
        return {"assets": records, "count": len(records)}
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc)


# ─────────────────────────── Write tools ────────────────────────────────────


@mcp.tool()
def generate_asset(
    brand: str,
    asset: str,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
    count: int = 1,
) -> dict[str, Any]:
    """Generate a brand asset and persist it with a sidecar catalog record.

    Routes through the same engine the CLI and REST API use: load the brand,
    compose the prompt, resolve references, pick the backend, generate, and save
    the image plus its sidecar JSON under brand-kits/<slug>/generated/<asset>/.

    If the chosen backend has no credentials configured it returns
    {"needs_key": "<backend>"} instead of failing, so a client can prompt for
    the key and retry.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        asset: Asset type key, for example "og_image". See list_asset_types.
        variant: Optional variant name allowed by the asset's prompt template.
        topic: Optional topic, used by text-heavy assets like og_image.
        platform: Optional platform hint, used by social_card.
        backend: Optional backend override (gemini, openai, recraft, flux).
        count: How many images to generate (default 1).

    Returns the written file paths, the backend and model used, the cost in
    cents, the size, and the prompt that produced them.
    """
    try:
        outcome = engine.generate_asset(
            brand,
            asset,
            variant=variant,
            topic=topic,
            platform=platform,
            backend=backend,
            count=count,
            dry_run=False,
        )
    except BackendNotConfigured:
        # Recompose dry to learn which backend was selected, so the client knows
        # exactly which key to supply.
        chosen = backend
        if chosen is None:
            try:
                chosen = engine.generate_asset(
                    brand,
                    asset,
                    variant=variant,
                    topic=topic,
                    platform=platform,
                    backend=backend,
                    dry_run=True,
                ).built.backend
            except Exception:  # noqa: BLE001 - best-effort resolution
                chosen = None
        return {"needs_key": chosen}
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, asset=asset)
    except BackendError as exc:
        return _error(exc, brand=brand, asset=asset)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, asset=asset)

    built = outcome.built
    result = outcome.result
    return {
        "brand": brand,
        "asset": asset,
        "variant": variant,
        "paths": [str(p) for p in outcome.paths],
        "backend": result.backend if result else built.backend,
        "model": result.model if result else None,
        "mime": result.mime if result else None,
        "cost_cents": result.cost_cents if result else None,
        "size": list(built.size),
        "prompt": built.text,
        "warnings": list(built.warnings),
        "missing_references": list(outcome.missing_refs),
    }


@mcp.tool()
def new_brand(
    slug: str,
    display_name: str,
    parent: str | None = None,
) -> dict[str, Any]:
    """Bootstrap a new brand kit from the blank template.

    Copies brand-kits/_template to brand-kits/<slug> and replaces the template
    placeholder tokens with the supplied slug, display name, and optional parent
    slug. The result is a brand.yaml ready to fill in (colors, style keywords,
    references). Fails cleanly if the slug already exists.

    Args:
        slug: Filesystem-safe identifier for the new brand, for example
            "majiks-studio".
        display_name: Human-readable brand name, for example "Majiks Studio".
        parent: Optional parent brand slug if this is a sub-brand. When set, the
            new brand inherits the parent's visual and voice defaults.

    Returns the created path and the brand.yaml path.
    """
    try:
        root = DEFAULT_KITS_DIR
        template = root / "_template"
        if not template.is_dir():
            return _error(f"template not found at {template}")

        dest = root / slug
        if dest.exists():
            return _error(f"brand {slug!r} already exists at {dest}", slug=slug)

        shutil.copytree(template, dest)

        brand_yaml = dest / "brand.yaml"
        text = brand_yaml.read_text()
        replacements = {
            "REPLACE-slug": slug,
            "REPLACE Display Name": display_name,
        }
        for token, value in replacements.items():
            text = text.replace(token, value)
        if parent:
            text = text.replace("parent: null", f"parent: {parent}")
        brand_yaml.write_text(text)

        return {
            "slug": slug,
            "created": str(dest),
            "brand_yaml": str(brand_yaml),
            "parent": parent,
        }
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)


# ─────────────────────────── Resources ──────────────────────────────────────


@mcp.resource("brand-ops://brands")
def brands_resource() -> str:
    """All seeded brand slugs as JSON."""
    slugs = _list_brands()
    return json.dumps({"brands": slugs, "count": len(slugs)}, indent=2)


@mcp.resource("brand-ops://asset-types")
def asset_types_resource() -> str:
    """The asset-type registry as JSON."""
    return json.dumps(list_asset_types(), indent=2)


# ─────────────────────────── Entrypoint ─────────────────────────────────────


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
