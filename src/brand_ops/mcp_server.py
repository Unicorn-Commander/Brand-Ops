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

import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP

from . import agent_identity, engine
from .agents.brand_intake import BrandIntakeAgent
from .agents.brand_research import _slugify
from .api.catalog import read_assets
from .api.guidelines import build_guidelines
from .api.schemas import BrandGuidelines
from .asset_types import ASSET_TYPES
from .backends.base import BackendError, BackendNotConfigured
from .brand_clone import clone_brand as _clone_brand
from .brand_clone import create_brand_from_draft as _create_brand_from_draft
from .brand_loader import (
    DEFAULT_KITS_DIR,
    BrandNotFound,
    get_allowed_brands,
    get_current_workspace_id,
    load_brand,
    resolve_reference_images,
)
from .brand_loader import (
    list_brands as _list_brands,
)
from .compliance import (
    build_rules,
    build_tokens,
    check_asset,
    check_text_target,
)
from .models import Brand
from .templates import list_templates as _list_templates

# stateless_http=True is REQUIRED for tenant isolation on the HTTP transport.
# In stateful mode FastMCP runs each tool handler on a long-lived task spawned at
# session `initialize`, BEFORE the per-request workspace scope is set in the ASGI
# task -- and a ContextVar only snapshots at task creation, so the tool would run
# UNSCOPED (every tenant's brands). Stateless mode starts the server task from
# within the per-request call, AFTER mcp_http sets the scope, so the tool task
# inherits the caller's allow-list. The stdio transport is unaffected.
mcp = FastMCP("brand-ops", stateless_http=True)

# Exceptions the engine and loader raise for caller mistakes. We translate each
# into a structured error rather than letting it cross the MCP boundary.
_USER_ERRORS = (BrandNotFound, KeyError, ValueError)

_log = logging.getLogger("brand_ops.mcp_server")


def _index_brand_best_effort(brand: Brand) -> None:
    """DB-index an MCP-created brand under the active workspace, best effort.

    Mirrors the REST create path's ``_index_brand``: a scoped create (the HTTP
    MCP resolves a workspace per request) records the new brand so it joins the
    workspace catalog this request, not only at the next boot import. Unscoped
    (stdio / CLI: no active workspace) is a no-op -- the file IS the catalog. The
    file write already succeeded and is workspace-stamped, so a DB error here
    only delays the brand's cross-request appearance; the boot import is the
    backstop, so this never fails the create. The MCP tools run with no live
    event loop (intake_brand already uses asyncio.run), so a fresh loop is safe.
    """
    # Lazy import: a module-level import of db.tenancy pulls
    # api.app -> mcp_http -> mcp_server back in before ``mcp`` is defined here (a
    # circular import). At call time the cycle is long resolved.
    from .db.tenancy import record_brand

    workspace_id = get_current_workspace_id()
    if not workspace_id:
        return
    meta = brand.brand
    try:
        asyncio.run(
            record_brand(
                workspace_id,
                slug=meta.slug,
                display_name=meta.display_name,
                parent_slug=meta.parent,
                tagline=meta.tagline,
                created_by="mcp",
            )
        )
    except Exception:  # noqa: BLE001 - best effort; the boot import is the backstop
        _log.warning(
            "MCP-created brand %s not DB-indexed; boot import will pick it up",
            meta.slug,
        )


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


# Lifecycle states that do NOT establish coverage for an asset type: a flagged
# (needs_review) or retired (archived) asset still leaves its type a gap. Mirrors
# the frontend coverage rule in web/src/lib/coverage.ts (isCovering) so the
# agent path and the human path agree on what "covered" means.
_NON_COVERING_STATUSES = ("needs_review", "archived")


def _covered_asset_types(brand_slug: str) -> set[str]:
    """The asset-type keys this brand already has an on-brand (covering) asset of.

    Reads the same on-disk catalog the REST /assets endpoint reads, then keeps a
    type once it has at least one asset whose lifecycle_status is not
    needs_review/archived (so approved, changes_requested, and an ungated null all
    count). Unknown asset types in the catalog are ignored.
    """
    covered: set[str] = set()
    for record in read_assets(brand=brand_slug):
        if record.asset_type not in ASSET_TYPES:
            continue
        if record.lifecycle_status in _NON_COVERING_STATUSES:
            continue
        covered.add(record.asset_type)
    return covered


def _missing_asset_types(brand_slug: str) -> list[str]:
    """The asset types this brand has no covering asset of, in registry order."""
    covered = _covered_asset_types(brand_slug)
    return [key for key in ASSET_TYPES if key not in covered]


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
def list_templates() -> dict[str, Any]:
    """List the locked, data-driven layout templates the Studio renders on-brand.

    A template is not an image and no backend is called: it is a layout the
    frontend draws on a canvas using the active brand's design tokens. Each entry
    carries an id, label, the catalog asset_type a saved render maps to, the pixel
    width and height, a background (brand-gradient or a solid color token), and
    the slots. Slots are positioned by 0..1 fractions of the canvas so the layout
    scales, and every color is a brand token name (brand/primary,
    text/foreground, …), never raw hex, so the output is on brand by
    construction. Editing is limited to text slot content; fonts, colors,
    positions, and the mark are locked. This is the same set GET /api/templates
    serves.

    Returns a dict with a ``templates`` list and a ``count``.
    """
    try:
        templates = _list_templates()
        return {"templates": templates, "count": len(templates)}
    except Exception as exc:  # noqa: BLE001 - boundary guard, never raise
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

        # Tenant gate: the same workspace allow-list the brand loader uses, so the
        # scoped MCP surface never lists another workspace's catalog. Unscoped
        # (None: stdio / CLI / tests) every brand is read, exactly as before.
        allowed = get_allowed_brands()
        if brand:
            if allowed is not None and brand not in allowed:
                return {"assets": [], "count": 0}
            brand_dirs = [root / brand] if (root / brand).is_dir() else []
        else:
            brand_dirs = [
                p
                for p in root.iterdir()
                if p.is_dir()
                and not p.name.startswith("_")
                and (allowed is None or p.name in allowed)
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


# ─────────────────────────── Agent-identity tools ───────────────────────────


@mcp.tool()
def list_brand_agents(brand: str) -> dict[str, Any]:
    """List a brand's agent identities, newest first.

    An agent identity belongs to exactly one brand and inherits its voice and
    visual language, so any agent a company spins up (a Steward, a Specialist, or
    a custom agent) is on brand by construction. This is the same roster GET
    /api/brands/{slug}/agents serves.

    Args:
        brand: Brand slug, for example "magic-unicorn".

    Returns a dict with an ``agents`` list (each carrying slug, name, role, bio,
    voice_tone, avatar_url, variant URLs, compliance, and governance_status) and a
    ``count``.
    """
    try:
        agents = agent_identity.list_agents(brand)
        return {"agents": agents, "count": len(agents)}
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)


@mcp.tool()
def get_agent_manifest(brand: str, agent: str) -> dict[str, Any]:
    """Get a brand agent's Brigade/suite-consumable identity manifest.

    The portable, machine-readable identity record other suite modules and the
    Brigade autonomy agents consume: schema, name, role, bio, brand, the avatar
    URL plus the variant state URLs and servable size derivatives, the voice tone,
    the brand palette, and the avatar's compliance verdict. The same document GET
    /api/brands/{slug}/agents/{agent}/manifest serves.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        agent: Agent slug, for example "iris". See list_brand_agents.
    """
    try:
        return agent_identity.build_manifest(brand, agent)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, agent=agent)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, agent=agent)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, agent=agent)


@mcp.tool()
def get_agent_team(brand: str) -> dict[str, Any]:
    """Get a brand's agent team: the Director, the brand Steward, and its agents.

    A per-brand rollup. ``director`` is the single Director persona (Iris) every
    brand shares; ``steward`` is this brand's dedicated agent (named for the brand,
    its avatar the brand's canonical mark); ``agents`` is the list of created agent
    identities. The ``governance`` block tallies those agents by avatar compliance
    bucket — on_brand, needs_review, or unrated — so a view can show coverage at a
    glance. The same rollup GET /api/brands/{slug}/agents/team serves.

    Args:
        brand: Brand slug, for example "magic-unicorn".
    """
    try:
        return agent_identity.build_team(brand)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)


# ─────────────────────────── Compliance tools ───────────────────────────────


@mcp.tool()
def get_brand_tokens(slug: str) -> dict[str, Any]:
    """Get a brand's machine-readable design tokens.

    Returns the semantic color tokens (brand/primary, surface/background, …)
    each with its hex and RGB, the typography tokens (font/display, font/body,
    font/mono), and a ready-to-paste ``css`` block of :root custom properties.
    Only colors the brand actually defines are included.

    Args:
        slug: Brand slug, for example "magic-unicorn".
    """
    try:
        brand = load_brand(slug)
        return build_tokens(brand)
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)


@mcp.tool()
def get_brand_rules(slug: str) -> dict[str, Any]:
    """Get a brand's deterministic compliance rules manifest.

    Returns the allowed color palette, the color tolerance and gradient axis the
    pixel check uses, the banned and required copy terms, the voice tone, and the
    human-judged manual rules. This is exactly what check_compliance scores
    against.

    Args:
        slug: Brand slug, for example "magic-unicorn".
    """
    try:
        brand = load_brand(slug)
        return build_rules(brand)
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)


@mcp.tool()
def get_brand_guidelines(slug: str) -> dict[str, Any]:
    """Get a brand's full guidelines document: the assembled brand book.

    One read-only call that returns the brand identity (slug, display name,
    tagline, description, parent, and the canonical mark URL), the design tokens
    (colors with hex and rgb, plus typography), the deterministic rules manifest,
    the voice (tone and forbidden phrases), the logo section (the mark URL and
    every logo-type lockup), and every catalog asset grouped by type in the
    asset-type registry order with its label and count. This is the same brand
    book GET /api/brands/{slug}/guidelines serves, so agents and other suite
    modules read one consistent brand truth. ``generated_at`` is null.

    Args:
        slug: Brand slug, for example "magic-unicorn".
    """
    try:
        doc = build_guidelines(slug)
        # Validate-and-dump through the wire model so the MCP payload matches the
        # REST response exactly (AssetRecord objects become plain dicts).
        return BrandGuidelines.model_validate(doc).model_dump(mode="json")
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)


@mcp.tool()
def check_compliance(
    brand: str,
    file: str | None = None,
    asset: str | None = None,
    kind: str = "generated",
    text: str | None = None,
) -> dict[str, Any]:
    """Score an asset or a piece of copy against a brand's rules. Deterministic.

    No LLM and no spend: the same input always yields the same verdict. Provide
    ``text`` to check copy (banned terms, em-dash, required terms), or ``file``
    with its ``asset`` type to check a catalog asset's color palette and its
    recorded prompt.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        file: Optional asset file name to check (requires ``asset``).
        asset: Asset type key the file lives under, for example "logo".
        kind: Which tree the file is in, "generated" (default) or "imported".
        text: Optional piece of copy to check instead of a file.

    Returns a result with the overall status (pass|warn|fail|skipped), a 0..100
    score, the per-rule checks, and human-readable violation lines.
    """
    try:
        loaded = load_brand(brand)
        rules = build_rules(loaded)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)

    try:
        if text is not None:
            return check_text_target(brand, text, rules)
        if file:
            if not asset:
                return _error("checking a file requires the asset type", brand=brand)
            if kind not in ("generated", "imported"):
                return _error("kind must be generated or imported", brand=brand)
            return check_asset(brand, file, asset, kind, rules)
        return _error("provide either text or file to check", brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)


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
def generate_card(
    brand: str,
    asset: str,
    message: str | None = None,
    mark: str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """Compose a text-bearing card (og_image or social_card) deterministically.

    Unlike generate_asset, the text is rendered with the brand's real fonts and
    exact colors over a brand background with the mark composited in, so the copy
    is always correct and on brand: no model text rendering, no provider key, no
    cost. Saves into the same catalog as generate_asset (shows in the GUI, gets a
    compliance stamp).

    Args:
        brand: Brand slug, for example "meeting-ops".
        asset: "og_image" or "social_card".
        message: Main copy (headline for og_image, tagline for social_card).
            Defaults to the brand tagline.
        mark: Optional mark to composite: an absolute path, or a filename under
            the brand's generated/imported logo dir. Defaults to the brand's
            declared mark, else its most recent logo.
        variant: Optional variant label recorded on the asset.

    Returns the written paths, the resolved mark, the size, and the message used.
    """
    try:
        outcome = engine.generate_card(
            brand, asset, message=message, mark=mark, variant=variant, dry_run=False
        )
    except _USER_ERRORS as exc:
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
        "backend": "compose",
        "mime": result.mime if result else "image/png",
        "size": list(built.size),
        "message": built.text,
        "mark": (result.meta.get("mark") if result else None),
        "notes": list(outcome.notes),
    }


@mcp.tool()
def generate_brand_kit(
    brand: str,
    only_missing: bool = True,
    asset_types: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a whole on-brand asset kit for a brand in one call.

    The batch form of generate_asset: it resolves a set of asset types and
    generates one of each through the same engine (so every image is on brand by
    construction and compliance-stamped on save), instead of the caller looping
    one type at a time. Each type costs gateway spend, so this is an explicit
    action.

    Targeting:
      - ``asset_types`` given: generate exactly those types (validated against the
        registry), regardless of ``only_missing``.
      - else ``only_missing`` true (the default): generate only the types this
        brand has no on-brand (covering) asset of yet — the coverage worklist,
        computed from the same catalog GET /api/assets reads (a type is covered
        when it has an asset whose lifecycle_status is not needs_review/archived).
      - else: generate all of the registry's asset types.

    Generation is per-type and fault-isolated: a failure on one type (a backend
    error, a missing key, a bad type) is captured in that type's result with
    ``status`` "error" and never aborts the rest of the kit. Nothing raises across
    the MCP boundary.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        only_missing: When true (default) and no explicit ``asset_types`` are
            given, target only the asset types with no covering asset yet.
        asset_types: Optional explicit list of asset-type keys to generate (for
            example ["logo", "og_image"]). Overrides ``only_missing``.

    Returns ``brand``, a ``results`` list (one entry per targeted type with
    ``asset_type`` and ``status`` "ok" or "error"; on ok also ``file``, ``url``,
    and the ``compliance`` summary; on error an ``error`` message), and the
    ``generated`` and ``failed`` counts. An unknown brand or an unknown explicit
    asset type is returned as a structured error, not raised.
    """
    # Resolve the brand once so an unknown brand is one clean error, not six.
    try:
        load_brand(brand)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)

    # Resolve the target asset types.
    if asset_types is not None:
        targets = [str(t) for t in asset_types]
        unknown = [t for t in targets if t not in ASSET_TYPES]
        if unknown:
            known = ", ".join(ASSET_TYPES)
            return _error(
                f"unknown asset type(s): {', '.join(unknown)}. Known types: {known}",
                brand=brand,
            )
    elif only_missing:
        try:
            targets = _missing_asset_types(brand)
        except Exception as exc:  # noqa: BLE001 - boundary guard
            return _error(exc, brand=brand)
    else:
        targets = list(ASSET_TYPES)

    results: list[dict[str, Any]] = []
    generated = 0
    failed = 0
    for asset_type in targets:
        # Each type runs through the same single-asset tool, so the kit path and
        # the one-off path can never drift. A needs_key payload is a per-type
        # failure (no credentials), captured like any other error.
        single = generate_asset(brand, asset_type)
        needs_key = single.get("needs_key")
        if needs_key is not None:
            failed += 1
            results.append(
                {
                    "asset_type": asset_type,
                    "status": "error",
                    "error": f"backend {needs_key!r} is not configured",
                    "needs_key": needs_key,
                }
            )
            continue
        if "error" in single:
            failed += 1
            results.append(
                {
                    "asset_type": asset_type,
                    "status": "error",
                    "error": single["error"],
                }
            )
            continue

        generated += 1
        paths = single.get("paths") or []
        # Surface a servable URL for the first written file, the same
        # /api/assets/file shape the catalog uses, so a caller can render it.
        url: str | None = None
        compliance: dict[str, Any] | None = None
        if paths:
            file_name = Path(paths[0]).name
            url = "/api/assets/file?" + urlencode(
                {"brand": brand, "asset": asset_type, "file": file_name, "kind": "generated"}
            )
            # Read back the just-written sidecar's compliance summary (advisory).
            for record in read_assets(brand=brand, asset=asset_type):
                if record.file == file_name:
                    compliance = (
                        record.compliance.model_dump(mode="json")
                        if record.compliance is not None
                        else None
                    )
                    break
        results.append(
            {
                "asset_type": asset_type,
                "status": "ok",
                "file": paths[0] if paths else None,
                "url": url,
                "compliance": compliance,
            }
        )

    return {
        "brand": brand,
        "results": results,
        "generated": generated,
        "failed": failed,
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


@mcp.tool()
def clone_brand(
    slug: str,
    new_slug: str,
    display_name: str,
    as_child: bool = True,
) -> dict[str, Any]:
    """Clone an existing brand into a new sub-brand or a distinct seeded brand.

    Writes brand-kits/<new_slug>/brand.yaml from <slug>. When ``as_child`` is
    true (the default) the new brand is a minimal child that inherits <slug>'s
    visual and voice through the loader's parent merge, so it tracks the parent;
    when false the source's resolved visual, voice, and prompt templates are
    copied in and the new brand has no parent, free to diverge. The source's
    canonical mark is never copied — a new brand has no logo yet — so the
    returned identity carries the parent slug but no mark.

    Args:
        slug: The existing source brand to clone, for example "magic-unicorn".
        new_slug: The new brand's identifier (lowercase letters, digits, and
            hyphens), for example "magic-unicorn-labs".
        display_name: Human-readable name for the new brand.
        as_child: True (default) inherits the source via the parent merge; False
            copies the source's visual, voice, and prompts in with no parent.

    Returns the new brand's identity (the same shape get_brand returns). A
    duplicate new_slug or an invalid slug is returned as a structured error, not
    raised across the boundary.
    """
    try:
        brand = _clone_brand(slug, new_slug, display_name, as_child=as_child)
    except FileExistsError as exc:
        return _error(exc, slug=slug, new_slug=new_slug)
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug, new_slug=new_slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug, new_slug=new_slug)
    _index_brand_best_effort(brand)
    return _brand_identity(new_slug, brand)


@mcp.tool()
def intake_brand(
    name: str,
    description: str | None = None,
    url: str | None = None,
    parent: str | None = None,
    mode: str = "describe",
) -> dict[str, Any]:
    """Draft a brand identity with the Director and create the brand.

    The agent-facing real-intake door: from a brand name and whatever you can
    say about it, the Director drafts a full identity (palette, typography,
    voice, style keywords, tagline, description) and a complete, valid brand kit
    is written from it under brand-kits/<slug>/. The slug is derived from the
    name (lowercase letters, digits, and hyphens). When ``parent`` is set the
    draft is kept consistent with the parent's palette and voice and the new
    brand records the parent. ``mode=onboard`` with a ``url`` drafts from the url
    and name as hints and notes that deep live-site capture needs Brigade.

    When the LLM gateway is not configured or returns an unusable draft, a
    neutral, tasteful, on-theme default identity is written instead and a note
    says it is a starting point to refine. The brand is always created.

    Args:
        name: The new brand's display name, for example "Majiks Studio".
        description: What you can say about the brand, if anything.
        url: A related URL or the brand's live site, if any.
        parent: An existing brand slug if this is a sub-brand.
        mode: "describe" (draft from the name and description, the default) or
            "onboard" (draft from the url and name as hints).

    Returns the created brand's identity (the same shape get_brand returns)
    under ``brand``, the drafted identity under ``draft``, and honest ``notes``.
    A name with no usable slug, a duplicate slug, or an invalid drafted color is
    returned as a structured error, not raised across the boundary.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        return _error("name is required")
    if not any(ch.isalnum() for ch in clean_name):
        return _error(
            f"name {clean_name!r} does not yield a valid slug: use letters or "
            "digits so a lowercase-hyphen slug can be derived",
            name=clean_name,
        )
    slug = _slugify(clean_name)
    if (DEFAULT_KITS_DIR / slug).exists():
        # Non-leaky existence check (matches brand_clone._assert_slug_available):
        # a scoped caller who is not a member of the brand's workspace gets a
        # generic "unavailable", never confirmation it exists in another tenant.
        allowed = get_allowed_brands()
        if allowed is not None and slug not in allowed:
            return _error("that brand name is unavailable", slug=slug)
        return _error(f"brand {slug!r} already exists", slug=slug)

    parent_slug = (parent or "").strip() or None
    parent_identity: dict[str, Any] | None = None
    if parent_slug:
        try:
            loaded_parent = load_brand(parent_slug)
        except _USER_ERRORS:
            loaded_parent = None
        if loaded_parent is not None:
            parent_identity = _brand_identity(parent_slug, loaded_parent)

    try:
        agent = BrandIntakeAgent()
        draft, notes = asyncio.run(
            agent.draft_identity(
                clean_name,
                description=(description or "").strip(),
                url=(url or "").strip(),
                parent_brand=parent_identity,
                mode=(mode or "describe").strip().lower(),
            )
        )
    except Exception as exc:  # noqa: BLE001 - drafting must not raise across the boundary
        # An unexpected drafting failure still creates the brand from the
        # neutral default, mirroring the REST route's degrade.
        draft, notes = BrandIntakeAgent().default_draft_with_note(
            clean_name, parent_identity
        )
        notes.append(f"Drafting hit an error and fell back to a default: {exc}")

    if parent_slug and parent_identity is None:
        notes.append(
            f"I could not load the parent brand {parent_slug!r}, so I drafted "
            "without its palette. The new brand still records it as parent."
        )

    # The operator's typed name is the display name (the draft carries an
    # identity, not a name), so stamp it before the write.
    draft["display_name"] = clean_name

    try:
        brand = _create_brand_from_draft(slug, draft, parent=parent_slug)
    except FileExistsError as exc:
        return _error(exc, slug=slug)
    except _USER_ERRORS as exc:
        return _error(exc, slug=slug)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, slug=slug)

    _index_brand_best_effort(brand)
    return {
        "brand": _brand_identity(slug, brand),
        "draft": draft,
        "notes": notes,
    }


@mcp.tool()
def create_agent(
    brand: str,
    name: str,
    role: str,
    bio: str = "",
) -> dict[str, Any]:
    """Create an on-brand agent identity for a brand. Inherits the brand voice.

    The agent-facing door to spin up a Steward, a Specialist, or a custom agent
    that comes out on brand by construction: it belongs to the brand and inherits
    the brand's voice tone. The slug is derived from the name. No avatar is
    generated here — call generate_agent_avatar (or generate_agent_variants) next.
    Routes through the same agent_identity layer the REST API and Studio use, so
    the agent path and the human path can never drift.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        name: The agent's display name, for example "Iris".
        role: The agent's role, for example "brand steward".
        bio: Optional short bio for the agent.

    Returns the created identity (slug, name, role, bio, inherited voice_tone,
    avatar_url null, compliance null, governance_status "unrated"). A duplicate
    agent, an unknown brand, or a name that yields no valid slug is returned as a
    structured error, not raised across the boundary.
    """
    try:
        return agent_identity.create_agent(brand, name, role, bio)
    except FileExistsError as exc:
        return _error(exc, brand=brand, name=name)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, name=name)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, name=name)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, name=name)


@mcp.tool()
def generate_agent_avatar(brand: str, agent: str) -> dict[str, Any]:
    """Generate an on-brand avatar for an agent, save it, and compliance-check it.

    Generates a single primary avatar (avatar.png) through the same backend
    resolution every other on-brand image uses, saves it under the agent's
    directory, and runs the brand color compliance check on it (advisory — a
    failing check never blocks the save). Routes through the same agent_identity
    layer the REST API and Studio use.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        agent: Agent slug, for example "iris". See list_brand_agents.

    Returns the refreshed identity with avatar_url set and the compliance verdict.
    An unknown brand or agent is returned as a structured error, not raised across
    the boundary.
    """
    try:
        return agent_identity.generate_avatar(brand, agent)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, agent=agent)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, agent=agent)
    except (BackendNotConfigured, BackendError) as exc:
        return _error(exc, brand=brand, agent=agent)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, agent=agent)


@mcp.tool()
def generate_agent_variants(brand: str, agent: str) -> dict[str, Any]:
    """Generate an agent's full on-brand avatar state set and record it.

    Generates the whole avatar STATE set (primary, thinking, success, alert) — one
    recognizable character on brand by construction, only the expression changes —
    saves each (primary -> avatar.png, the rest -> avatar-<state>.png),
    compliance-checks each (advisory), and writes the variants map onto the agent
    while keeping the primary as the canonical avatar. Routes through the same
    agent_identity layer the REST API and Studio use.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        agent: Agent slug, for example "iris". See list_brand_agents.

    Returns the refreshed identity carrying the variant state URLs. An unknown
    brand or agent is returned as a structured error, not raised across the
    boundary.
    """
    try:
        return agent_identity.generate_variants(brand, agent)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, agent=agent)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, agent=agent)
    except (BackendNotConfigured, BackendError) as exc:
        return _error(exc, brand=brand, agent=agent)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, agent=agent)


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


@mcp.resource("brand-ops://templates")
def templates_resource() -> str:
    """The locked template registry as JSON."""
    return json.dumps(list_templates(), indent=2)


# ─────────────────────────── Entrypoint ─────────────────────────────────────


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
