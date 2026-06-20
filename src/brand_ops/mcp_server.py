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
import base64
import binascii
import json
import logging
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

from . import agent_identity, engine, voices
from .activity import append_event, read_events
from .agents.brand_intake import BrandIntakeAgent
from .agents.brand_research import _slugify
from .agents.registry import AGENT_REGISTRY
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
    current_kits_dir,
    get_allowed_brands,
    get_current_workspace_id,
    load_brand,
    resolve_reference_images,
    safe_subpath,
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

# Tool annotations (MCP ToolAnnotations) advertise each tool's behavior to the
# client so an agent can reason about safety before it calls. They are hints, not
# a gate -- the real authorization is the bearer + workspace scope in mcp_http.
#
#   _read    a read-only tool: no state changes, safe to call freely and retry.
#   _write   a non-read, non-spend write (filesystem brand/agent records,
#            lifecycle moves). Not read-only; idempotency varies, so left unset.
#   _spend   a write that calls the metered image gateway: every call may incur
#            real spend. Marked destructive (the spend is the side effect) and
#            NOT idempotent (each call produces new images), so a client treats a
#            retry as another charge. The cost note lives in the description.
#
# title is the human label a client UI shows; openWorldHint stays the SDK default
# (the tools act on local brand state, not an open external world).
def _read(title: str) -> ToolAnnotations:
    return ToolAnnotations(title=title, readOnlyHint=True)


def _write(title: str, *, idempotent: bool | None = None) -> ToolAnnotations:
    return ToolAnnotations(
        title=title, readOnlyHint=False, destructiveHint=False, idempotentHint=idempotent
    )


def _spend(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title, readOnlyHint=False, destructiveHint=True, idempotentHint=False
    )


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


@mcp.tool(annotations=_read("List brands"))
def list_brands() -> dict[str, Any]:
    """List the brand slugs seeded under brand-kits/.

    Returns a dict with a ``brands`` list of slugs and a ``count``.
    """
    try:
        slugs = _list_brands()
        return {"brands": slugs, "count": len(slugs)}
    except Exception as exc:  # noqa: BLE001 - boundary guard, never raise
        return _error(exc)


@mcp.tool(annotations=_read("Get brand identity"))
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


@mcp.tool(annotations=_read("List asset types"))
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


@mcp.tool(annotations=_read("List templates"))
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


@mcp.tool(annotations=_read("Build prompt (dry run)"))
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


@mcp.tool(annotations=_read("List generated assets"))
def list_generated_assets(
    brand: str | None = None,
    asset: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """List previously generated assets from the on-disk catalog.

    Reads the sidecar JSON records written next to each generated image under
    brand-kits/<slug>/generated/<asset>/. Each record captures how the asset was
    made: prompt, backend, model, size, cost, and references.

    Args:
        brand: Optional brand slug to restrict the listing to one brand.
        asset: Optional asset-type key to restrict the listing to one type.
        status: Optional lifecycle filter, "approved" or "needs_review", matching
            GET /api/assets. A never-stamped (null) asset is excluded by any
            status filter. An unrecognized value is returned as a structured error.

    Returns a list of records, each including the image path it describes.
    """
    if status is not None and status not in ("approved", "needs_review"):
        return _error("status must be approved or needs_review")
    try:
        root = current_kits_dir()
        if not root.exists():
            return {"assets": [], "count": 0}

        # Tenant gate: the same workspace allow-list the brand loader uses, so the
        # scoped MCP surface never lists another workspace's catalog. Unscoped
        # (None: stdio / CLI / tests) every brand is read, exactly as before.
        allowed = get_allowed_brands()
        if brand:
            if allowed is not None and brand not in allowed:
                return {"assets": [], "count": 0}
            bd = safe_subpath(root, brand)
            brand_dirs = [bd] if (bd is not None and bd.is_dir()) else []
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
                ad = safe_subpath(gen_root, asset)
                asset_dirs = [ad] if (ad is not None and ad.is_dir()) else []
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
                    # Lifecycle filter: drop a record whose status differs (a
                    # never-stamped null is dropped by any filter), mirroring the
                    # catalog's read_assets(status=...) and GET /api/assets.
                    if status is not None and record.get("lifecycle_status") != status:
                        continue
                    # The image the sidecar describes is the sidecar minus .json.
                    record["image_path"] = str(sidecar.with_suffix(""))
                    record["sidecar_path"] = str(sidecar)
                    records.append(record)
        return {"assets": records, "count": len(records)}
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc)


# ─────────────────────────── Agent-identity tools ───────────────────────────


@mcp.tool(annotations=_read("List brand agents"))
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


@mcp.tool(annotations=_read("Get agent manifest"))
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


@mcp.tool(annotations=_read("Get agent team"))
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


@mcp.tool(annotations=_read("Get brand tokens"))
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


@mcp.tool(annotations=_read("Get brand rules"))
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


@mcp.tool(annotations=_read("Get brand guidelines"))
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


@mcp.tool(annotations=_read("Check compliance"))
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


@mcp.tool(annotations=_spend("Generate asset (incurs spend)"))
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

    This calls the metered image gateway, so every call may incur real spend
    (``count`` multiplies it) and is not idempotent: a retry generates another
    image and another charge. Routes through the same engine the CLI and REST API
    use: load the brand, compose the prompt, resolve references, pick the backend,
    generate, and save the image plus its sidecar JSON under
    brand-kits/<slug>/generated/<asset>/.

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


@mcp.tool(annotations=_write("Compose text card", idempotent=False))
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
    cost (this writes a catalog asset locally; it does not call the metered
    gateway). Saves into the same catalog as generate_asset (shows in the GUI,
    gets a compliance stamp).

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


@mcp.tool(annotations=_spend("Generate brand kit (incurs spend)"))
def generate_brand_kit(
    brand: str,
    only_missing: bool = True,
    asset_types: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a whole on-brand asset kit for a brand in one call.

    The batch form of generate_asset: it resolves a set of asset types and
    generates one of each through the same engine (so every image is on brand by
    construction and compliance-stamped on save), instead of the caller looping
    one type at a time. Each type calls the metered image gateway, so this may
    incur real spend (one charge per generated type) and is not idempotent: a
    re-run with the same targeting generates fresh images and fresh charges.

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


@mcp.tool(annotations=_write("Create brand from template", idempotent=False))
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
        # The template is a shared, global seed resource (a "_"-prefixed dir that
        # is not a tenant brand), so it is read from the global root; the new brand
        # is written into the active workspace's subtree.
        template = DEFAULT_KITS_DIR / "_template"
        if not template.is_dir():
            return _error(f"template not found at {template}")

        dest = current_kits_dir() / slug
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


@mcp.tool(annotations=_write("Clone brand", idempotent=False))
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


@mcp.tool(annotations=_write("Draft and create brand", idempotent=False))
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
    if (current_kits_dir() / slug).exists():
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


@mcp.tool(annotations=_write("Create agent identity", idempotent=False))
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


@mcp.tool(annotations=_spend("Generate agent avatar (incurs spend)"))
def generate_agent_avatar(brand: str, agent: str) -> dict[str, Any]:
    """Generate an on-brand avatar for an agent, save it, and compliance-check it.

    This calls the metered image gateway, so every call may incur real spend and
    is not idempotent: a retry generates a new avatar and another charge.
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


@mcp.tool(annotations=_spend("Generate agent variants (incurs spend)"))
def generate_agent_variants(brand: str, agent: str) -> dict[str, Any]:
    """Generate an agent's full on-brand avatar state set and record it.

    This calls the metered image gateway once per state, so it may incur real
    spend (several charges) and is not idempotent: a re-run regenerates the set
    and charges again. Generates the whole avatar STATE set (primary, thinking,
    success, alert), one recognizable character on brand by construction where
    only the expression changes, saving each (primary -> avatar.png, the rest ->
    avatar-<state>.png), compliance-checks each (advisory), and writes the
    variants map onto the agent while keeping the primary as the canonical
    avatar. Routes through the same agent_identity layer the REST API and Studio
    use.

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


# ─────────────────────────── Specialist-agent tools ─────────────────────────
# The 8 in-module specialists (director, brand_research, brand_intake, asset_pack,
# avatar, brand_critic, tagger, refine) the GUI runs via GET /api/agents and
# POST /api/agents/{name}/run. These two tools wrap that same registry + runner,
# so an MCP agent can list and drive every specialist a human can.


@mcp.tool(annotations=_read("List specialist agents"))
def list_agents() -> dict[str, Any]:
    """List the in-module specialist agents and their input contracts.

    The same roster GET /api/agents serves, projected from the one Brigade
    registry that also backs the REST surface, so the agent path and the human
    path can never disagree on the agent set. Each entry carries the stable name,
    a human description, the JSON-schema ``input_schema`` an agent payload must
    satisfy, the auth ``scopes_required`` a caller must hold, and the minimum
    ``tier_required``. Use a name and its input_schema with run_agent.

    Returns a dict with an ``agents`` list and a ``count``.
    """
    try:
        agents = [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "scopes_required": list(spec.scopes_required),
                "tier_required": spec.tier_required,
            }
            for spec in AGENT_REGISTRY
        ]
        return {"agents": agents, "count": len(agents)}
    except Exception as exc:  # noqa: BLE001 - boundary guard, never raise
        return _error(exc)


@mcp.tool(annotations=_write("Run a specialist agent", idempotent=False))
async def run_agent(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one specialist agent against a JSON payload and return its result.

    The agent-facing form of POST /api/agents/{name}/run. ``name`` is one of the
    specialists from list_agents (director, brand_research, brand_intake,
    asset_pack, avatar, brand_critic, tagger, refine); ``payload`` is that agent's
    input verbatim (see its ``input_schema`` from list_agents). Routes through the
    same registry-resolved class and runner the REST route uses, with the caller's
    workspace threaded for best-effort spend attribution (specialist/<name>), so
    the agent path and the human path can never drift.

    The specialists degrade gracefully when the LLM gateway or Brigade is absent:
    they return a result whose ``status`` is something like ``skipped``,
    ``research_only``, or ``ok`` rather than failing, and that is returned as-is.
    Some agents (asset_pack, avatar) plan against the metered gateway, so a
    non-dry-run payload may incur spend; default those payloads to ``dry_run``
    true unless you intend to generate.

    Args:
        name: The specialist agent name. See list_agents.
        payload: The agent's input dict (its shape is per-agent). Defaults to {}.

    Returns ``agent`` (the name), ``status`` (lifted from the agent's result), and
    ``result`` (the agent's full return dict, verbatim). An unknown agent name is
    returned as a structured error, and any agent failure is returned as a
    structured error, never raised across the boundary.
    """
    # Lazy import: api.agents pulls the api package (-> app -> mcp_http ->
    # mcp_server) which is a module-load cycle; at call time it is long resolved.
    # This is the same registry-backed runner POST /api/agents/{name}/run uses.
    from .api.agents import UnknownAgent
    from .api.agents import run_agent as _run_agent

    body = payload if isinstance(payload, dict) else {}
    workspace_id = get_current_workspace_id()
    brand_slug = body.get("brand")
    brand = brand_slug if isinstance(brand_slug, str) and brand_slug else None
    try:
        result = await _run_agent(name, body, workspace_id=workspace_id, brand_slug=brand)
    except UnknownAgent as exc:
        return _error(exc, agent=name)
    except Exception as exc:  # noqa: BLE001 - any agent failure is a structured error, never raised
        return _error(f"agent error: {exc}", agent=name)

    agent_status = result.get("status", "ok") if isinstance(result, dict) else "ok"
    return {
        "agent": name,
        "status": str(agent_status),
        "result": result if isinstance(result, dict) else {"value": result},
    }


@mcp.tool(annotations=_write("Plan a generation from free text", idempotent=False))
async def plan_generation(
    text: str,
    brand: str | None = None,
    asset: str | None = None,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Turn a free-text generation ask into a concrete, valid GenerateRequest.

    The agent-facing form of POST /api/plan-generation. From a plain instruction
    ("make an OG image for the investor update, dark") plus any control values you
    already know, the planner picks a brand, asset type, variant, topic, and
    platform from the real brand slugs and asset types this backend knows (the
    same workspace-scoped brand context the GUI sees), validates the pick, and
    returns a fully valid ``request`` you can feed straight into build_prompt or
    generate_asset, plus a short ``rationale`` and the fields it ``assumed``.

    No image is generated and no spend occurs here; this only plans. The planner
    degrades to a deterministic keyword heuristic when the gateway is unavailable,
    so it answers with a sensible plan rather than failing. An operator with no
    brands at all (nothing to generate for) is returned as a structured error.

    Args:
        text: The free-text generation instruction. Required.
        brand: Optional active brand slug to default to when the text is silent.
        asset: Optional asset-type key to keep.
        variant: Optional variant to keep.
        topic: Optional topic to keep.
        platform: Optional platform to keep.
        backend: Optional explicit backend override to keep.

    Returns ``request`` (a valid GenerateRequest dict), ``rationale``, and
    ``assumed``. A no-brand situation is a structured error, not raised.
    """
    from .api.routes import run_plan_generation

    try:
        return await run_plan_generation(
            text=text,
            brand=brand,
            asset=asset,
            variant=variant,
            topic=topic,
            platform=platform,
            backend=backend,
            workspace_id=get_current_workspace_id(),
        )
    except ValueError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc)


@mcp.tool(annotations=_write("Talk to the brand director", idempotent=False))
async def director_chat(
    message: str,
    brand: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Talk to Iris, the brand director, and get a reply plus a routed intent.

    The agent-facing form of POST /api/director/chat. Iris is the single
    conversational orchestrator over the Studio: she answers in persona and
    classifies the turn into an ``intent`` (``chat`` talk only; ``single`` to
    generate one asset; ``pack`` to run the asset_pack agent; ``research`` to run
    brand_research; ``agent`` to create or surface an on-brand agent identity;
    ``compliance`` to run the deterministic gate). For the ``agent`` and
    ``compliance`` intents she ACTS through the same shared flow the route uses
    (creating the identity, or scoring the catalog/copy), so the reply already
    reflects the result. Routes through the same Director flow with the caller's
    workspace threaded, so the agent path and the human path can never drift.

    Never fails on an LLM problem: an unreachable gateway degrades to a graceful
    in-persona ``chat`` reply.

    Args:
        message: The operator's new line to Iris. Required.
        brand: Active brand slug in the Studio, if any.
        history: Recent conversation turns, oldest first, each a dict with
            ``role`` ("user" or "agent") and ``text``. Optional.

    Returns the director reply: ``reply`` (Iris in first person), ``intent``,
    ``brand``, the scoped ``asset``/``variant``/``pack``/``topic`` (populated only
    for the intent that uses them), an ``agent`` dict (only for intent "agent"),
    and a ``compliance`` verdict (only for intent "compliance").
    """
    from .api.routes import run_director_chat

    try:
        return await run_director_chat(
            message=message,
            brand=brand,
            history=history or [],
            workspace_id=get_current_workspace_id(),
        )
    except Exception as exc:  # noqa: BLE001 - boundary guard; the route flow already degrades
        return _error(exc, brand=brand)


# ─────────────────────────── Asset lifecycle tools ──────────────────────────
# The approve / transition surface (POST /api/assets/approve, /transition) and
# the imported-asset upload (POST /api/assets/upload), so an MCP agent can move
# an asset through its lifecycle and bring an existing image into a brand kit.


def _transition_record(
    *,
    brand: str,
    file: str,
    asset: str,
    kind: str,
    to: str,
    note: str | None,
    actor: str,
) -> dict[str, Any]:
    """Drive a lifecycle transition through the route's shared boundary.

    Wraps the same ``_apply_transition`` POST /assets/approve and
    /assets/transition use (same sidecar write, same activity event, same
    workspace 404 guard), and projects its refreshed AssetRecord to a plain dict.
    The route helper raises ``HTTPException`` for bad input; map those to the
    structured MCP error shape rather than letting them cross the boundary.
    """
    from fastapi import HTTPException

    from .api.routes import _apply_transition

    if kind not in ("generated", "imported"):
        return _error("kind must be generated or imported", brand=brand)
    try:
        record = _apply_transition(
            brand=brand, file=file, asset=asset, kind=kind, to=to, note=note, actor=actor
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _error(detail, brand=brand, asset=asset, file=file)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, asset=asset, file=file)
    return {"asset": record.model_dump(mode="json")}


@mcp.tool(annotations=_write("Approve an asset", idempotent=True))
def approve_asset(
    brand: str,
    asset: str,
    file: str,
    kind: str = "generated",
) -> dict[str, Any]:
    """Operator override: flip an asset's lifecycle status to ``approved``.

    The agent-facing form of POST /api/assets/approve, a thin alias for
    transition_asset with ``to="approved"`` and ``actor="you"`` -- accepting an
    asset the deterministic checker flagged needs_review. It writes
    ``lifecycle_status="approved"`` into the sidecar (preserving every other key)
    and appends an "approved" activity event, through the same shared boundary the
    REST route uses.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        asset: Asset type key the file lives under, for example "logo".
        file: The asset file name to approve, for example "logo-wordmark.png".
        kind: Which tree the file is in, "generated" (default) or "imported".

    Returns the refreshed catalog record under ``asset`` (the same AssetRecord
    shape list_generated_assets and GET /api/assets serve). A path with no sidecar
    or an unknown kind is returned as a structured error, not raised.
    """
    return _transition_record(
        brand=brand, file=file, asset=asset, kind=kind, to="approved", note=None, actor="you"
    )


@mcp.tool(annotations=_write("Move an asset's lifecycle", idempotent=True))
def transition_asset(
    brand: str,
    asset: str,
    file: str,
    to: str,
    kind: str = "generated",
    note: str | None = None,
    actor: str = "mcp",
) -> dict[str, Any]:
    """Move an asset to a new lifecycle state and record it on the activity feed.

    The agent-facing form of POST /api/assets/transition. Locates the asset's
    sidecar in the kits tree and writes ``lifecycle_status=to`` into it, preserving
    every other key, then appends one activity event attributing the move to
    ``actor`` with an optional ``note``. Routes through the same shared boundary
    the REST route and approve_asset use, so the three can never drift.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        asset: Asset type key the file lives under, for example "logo".
        file: The asset file name to move, for example "logo-wordmark.png".
        to: Target lifecycle state: one of needs_review, approved,
            changes_requested, archived.
        kind: Which tree the file is in, "generated" (default) or "imported".
        note: Optional human reason recorded on the activity feed.
        actor: Who is acting (default "mcp", so an agent-driven move is attributed
            to the MCP surface rather than masquerading as the human "you").

    Returns the refreshed catalog record under ``asset``. A missing sidecar, an
    unknown ``kind``, or an invalid ``to`` is returned as a structured error, not
    raised across the boundary.
    """
    return _transition_record(
        brand=brand, file=file, asset=asset, kind=kind, to=to, note=note, actor=actor
    )


@mcp.tool(annotations=_write("Upload an asset into a brand kit", idempotent=False))
def upload_asset(
    brand: str,
    asset_type: str,
    file_path: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    variant: str | None = None,
) -> dict[str, Any]:
    """Bring an existing image into a brand kit's imported/ tree.

    The agent-facing form of POST /api/assets/upload. Supply the image either as
    ``file_path`` (an absolute path the server can read) or as ``file_base64`` (the
    raw bytes, base64-encoded) with a ``filename`` so the stored name and type can
    be derived. The bytes land at
    brand-kits/<brand>/imported/<asset_type>/<name> with a catalog sidecar
    (source="upload"), are compliance-stamped on landing, and come back in the same
    AssetRecord shape GET /api/assets serves. Routes through the same
    ``upload.save_upload`` core the REST route uses (same validation, same size
    cap, same sidecar), so the agent path and the human path can never drift.

    Args:
        brand: Brand slug the asset belongs to. Must exist.
        asset_type: Asset type key the image is, for example "logo". See
            list_asset_types.
        file_path: Absolute path to a readable image file. Use this OR file_base64.
        file_base64: The image bytes, base64-encoded. Use this OR file_path; when
            set, also pass ``filename`` so the stored name and type resolve.
        filename: The file name to store under (and, for file_base64, the source
            of the stored extension). Defaults to file_path's name when a path is
            given.
        content_type: Optional MIME type (image/png, image/jpeg, image/svg+xml,
            image/webp). When omitted it is inferred from the filename extension.
        variant: Optional variant label recorded on the asset.

    Returns the new catalog record under ``asset``. An unknown brand, an unknown
    asset type, an unreadable path, undecodable base64, a missing/oversized/
    unsupported file, or supplying neither/both sources is returned as a structured
    error, not raised across the boundary.
    """
    # Lazy import: upload pulls api.catalog -> the api package cycle; resolved at
    # call time. This is the same save_upload the REST upload route wraps.
    from .api.upload import MAX_UPLOAD_BYTES, UploadError, save_upload

    if bool(file_path) == bool(file_base64):
        return _error("provide exactly one of file_path or file_base64", brand=brand)

    # Gate brand existence FIRST, before reading or decoding any bytes, mirroring
    # the REST route (which loads the brand before reading the upload) so an
    # unknown or out-of-workspace brand is a clean error and a bad-brand upload
    # never does the work of reading a large file.
    try:
        load_brand(brand)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)

    # Resolve the bytes and the name from whichever source was given.
    if file_path:
        src = Path(file_path)
        try:
            data = src.read_bytes()
        except OSError as exc:
            return _error(f"could not read file_path: {exc}", brand=brand)
        name = filename or src.name
    else:
        # Strip surrounding whitespace so RFC-2045 line-wrapped base64 (what the
        # `base64` CLI and base64.encodebytes emit, with embedded newlines) decodes
        # too, not only single-line base64. validate=True still rejects genuinely
        # non-alphabet input.
        cleaned = "".join((file_base64 or "").split())
        try:
            data = base64.b64decode(cleaned, validate=True)
        except (binascii.Error, ValueError) as exc:
            return _error(f"file_base64 is not valid base64: {exc}", brand=brand)
        name = filename or ""
        if not name:
            return _error("file_base64 requires a filename", brand=brand)

    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        return _error(f"file is larger than the {mb} MB limit", brand=brand)

    # Infer the content type from the name when not supplied, mirroring how the
    # upload helper accepts an ambiguous type and falls back to the extension.
    ctype = content_type or _guess_content_type(name)
    try:
        record = save_upload(
            brand=brand,
            asset_type=asset_type,
            filename=name,
            content_type=ctype,
            data=data,
            variant=variant,
        )
    except UploadError as exc:
        return _error(exc.message, brand=brand, code=exc.code)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)
    return {"asset": record.model_dump(mode="json")}


# The image content types the upload accepts, by stored extension, so a path or a
# named base64 upload resolves a type without the caller having to state one.
_EXT_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _guess_content_type(name: str) -> str:
    """Best-effort image MIME from a filename extension, else octet-stream.

    The upload helper treats an unknown/ambiguous type by falling back to the
    name's extension, so returning octet-stream here still lets a well-named file
    through and lets a truly unsupported one be rejected cleanly downstream.
    """
    return _EXT_CONTENT_TYPE.get(Path(name).suffix.lower(), "application/octet-stream")


# ─────────────────────────── Agent persona / voice tools ────────────────────
# Editing an agent's persona text and binding its spoken voice (PATCH
# .../agents/{agent}, PUT .../voice, GET /api/voices), so an MCP agent can curate
# the agents it stands up, not only create them.


@mcp.tool(annotations=_read("List assignable voices"))
def list_voices() -> dict[str, Any]:
    """List the catalog of assignable agent voices.

    The same roster GET /api/voices serves: the curated TTS voices an agent can be
    bound to (each with id, label, engine, kind, and a short descriptor) plus
    whether live preview is wired. Use a voice id with set_agent_voice.

    Returns a dict with a ``voices`` list, a ``count``, and ``preview_available``.
    """
    try:
        roster = voices.list_voices()
        return {
            "voices": roster,
            "count": len(roster),
            "preview_available": voices.preview_available(),
        }
    except Exception as exc:  # noqa: BLE001 - boundary guard, never raise
        return _error(exc)


@mcp.tool(annotations=_write("Update an agent's persona", idempotent=True))
def update_agent(
    brand: str,
    agent: str,
    role: str | None = None,
    bio: str | None = None,
) -> dict[str, Any]:
    """Edit an agent's persona text (role and/or bio); avatar and voice untouched.

    The agent-facing form of PATCH /api/brands/{slug}/agents/{agent}. Only the
    fields you pass change; the avatar, voice, voice tone, and compliance are left
    alone. An empty role is rejected (every agent has a role); an empty bio clears
    the bio. Routes through the same agent_identity layer the REST API and Studio
    use, so the agent path and the human path can never drift.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        agent: Agent slug, for example "iris". See list_brand_agents.
        role: New role text, if changing it. An empty/whitespace role is rejected.
        bio: New bio text, if changing it. An empty string clears the bio.

    Returns the refreshed identity (the same shape list_brand_agents entries have).
    An unknown brand or agent, or an empty role, is returned as a structured error,
    not raised across the boundary.
    """
    if role is not None and not role.strip():
        return _error("agent role cannot be empty", brand=brand, agent=agent)
    try:
        ident = agent_identity.update_agent(brand, agent, role=role, bio=bio)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, agent=agent)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, agent=agent)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, agent=agent)
    # Record the edit on the provenance feed, the same as PATCH .../agents/{agent}
    # does, so the agent door and the human door leave the same audit trail. The
    # actor is "mcp" (the agent door) rather than the route's "iris".
    _emit_agent_event("updated agent", brand, ident.get("slug", agent))
    return ident


@mcp.tool(annotations=_write("Bind an agent's voice", idempotent=True))
def set_agent_voice(
    brand: str,
    agent: str,
    voice_id: str | None,
    engine: str | None = None,
    speed: float = 1.0,
) -> dict[str, Any]:
    """Bind (or clear) the agent's spoken voice from the catalog.

    The agent-facing form of PUT /api/brands/{slug}/agents/{agent}/voice. A
    non-empty ``voice_id`` must be one of the catalog voices (see list_voices),
    so an agent can only be bound to a voice orator actually serves; a null/empty
    ``voice_id`` clears the binding. The engine is resolved from the catalog when
    omitted. Routes through the same agent_identity layer the REST API and Studio
    use, so the agent path and the human path can never drift.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        agent: Agent slug, for example "iris". See list_brand_agents.
        voice_id: A catalog voice id (see list_voices), or null/empty to clear.
        engine: Optional engine override (resolved from the catalog when omitted).
        speed: Optional speech speed multiplier recorded on the binding.

    Returns the refreshed identity. An unknown brand, agent, or voice is returned
    as a structured error, not raised across the boundary.
    """
    clean = (voice_id or "").strip() or None
    if clean is not None and not voices.is_known(clean):
        return _error(f"unknown voice {clean!r}", brand=brand, agent=agent)
    try:
        ident = agent_identity.set_agent_voice(brand, agent, clean, engine=engine, speed=speed)
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand, agent=agent)
    except agent_identity.AgentIdentityError as exc:
        return _error(exc, brand=brand, agent=agent)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, agent=agent)
    # Record on the feed, the same as PUT .../agents/{agent}/voice does (set vs
    # cleared depending on whether a voice was bound), so both doors agree.
    _emit_agent_event(
        "set agent voice" if clean else "cleared agent voice",
        brand,
        ident.get("slug", agent),
    )
    return ident


def _emit_agent_event(action: str, brand: str, agent_slug: str) -> None:
    """Append one activity event for an MCP agent-identity edit. Best-effort.

    Mirrors the append_event the REST agent routes emit so the provenance feed
    records edits from both doors. Attributed to "mcp" (the agent door) rather
    than the route's "iris". Never raises: the feed is advisory and must never
    undo a write that already succeeded.
    """
    try:
        from datetime import UTC, datetime

        append_event(
            "mcp",
            "agent",
            action,
            brand=brand,
            target=agent_slug,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the write
        pass


# ─────────────────────────── Orientation tools ──────────────────────────────
# The provenance feed (GET /api/activity) and the brand-wide compliance rollup
# (GET /api/brands/{slug}/compliance), so an MCP agent can see what happened and
# audit a whole brand in one call.


@mcp.tool(annotations=_read("Read the activity feed"))
def read_activity(brand: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Read the append-only activity / provenance feed, newest first.

    The agent-facing form of GET /api/activity. Returns the recent events from the
    on-disk feed (who did what, when, to which asset), optionally filtered to one
    ``brand`` slug, capped at ``limit``. Mirrors the route's tenancy: a
    workspace-scoped caller sees only its own brands' events (a brand-less rollup
    event is dropped for it); unscoped (stdio / CLI) sees the global feed.

    Args:
        brand: Optional brand slug to filter to one brand's events.
        limit: Max events to return, 1..500 (default 50). Out-of-range is clamped.

    Returns a dict with an ``events`` list (newest first) and a ``count``.
    """
    try:
        capped = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        capped = 50
    try:
        # Mirror GET /api/activity's tenancy: scope to the workspace allow-list.
        allowed = get_allowed_brands()
        if allowed is None:
            events = read_events(brand=brand, limit=capped)
        elif brand is not None:
            events = read_events(brand=brand, limit=capped) if brand in allowed else []
        else:
            events = [
                e for e in read_events(brand=None, limit=500) if e.get("brand") in allowed
            ][:capped]
        return {"events": events, "count": len(events)}
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc)


@mcp.tool(annotations=_read("Audit a brand's compliance"))
def get_brand_compliance(brand: str) -> dict[str, Any]:
    """Run the deterministic compliance check over a brand's whole catalog.

    The agent-facing form of GET /api/brands/{slug}/compliance. Scores every
    catalog asset of the brand with the same engine check_compliance uses, then
    rolls the verdicts into a ``summary`` (pass/warn/fail/skipped counts and an
    average score) plus a per-asset ``assets`` row list. Routes through the same
    shared rollup the REST route uses, so the agent path and the human path can
    never drift. No LLM and no spend.

    Args:
        brand: Brand slug, for example "magic-unicorn".

    Returns ``brand``, the ``summary``, and the per-asset ``assets`` rows. An
    unknown brand is returned as a structured error, not raised.
    """
    from .api.routes import compute_brand_compliance

    try:
        return compute_brand_compliance(brand).model_dump(mode="json")
    except _USER_ERRORS as exc:
        return _error(exc, brand=brand)
    except Exception as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand)


# structured_output=False: this tool returns native MCP image content (an Image)
# on success and a structured error dict otherwise. FastMCP cannot build one JSON
# output schema for that Image|dict union, so opt out of structured output and let
# the tool emit image content or a JSON error as the call result.
@mcp.tool(annotations=_read("Fetch asset bytes as an image"), structured_output=False)
def fetch_asset_bytes(
    brand: str,
    asset: str,
    file: str,
    kind: str = "generated",
) -> Image | dict[str, Any]:
    """Fetch one catalog asset's bytes as MCP image content the agent can SEE.

    The agent-facing form of GET /api/assets/file, but returning native MCP image
    content (not a URL) so a vision-capable agent can actually view the output it
    or a human produced and judge it. Resolves
    brand-kits/<brand>/<kind>/<asset>/<file> and refuses anything that escapes the
    kits root, with the same workspace visibility guard the REST file route uses,
    so the scoped MCP surface never serves another workspace's bytes. Raster images
    (png/jpg/webp/gif) are returned as image content; a vector (svg) or any other
    type is returned as a structured error pointing at its on-disk path, since it
    is not raster image content.

    Args:
        brand: Brand slug, for example "magic-unicorn".
        asset: Asset type key the file lives under, for example "logo".
        file: The asset file name, for example "logo-wordmark.png".
        kind: Which tree the file is in, "generated" (default) or "imported".

    Returns MCP image content on success, or a structured error (unknown kind,
    out-of-workspace brand, missing file, or a non-raster type), never raised.
    """
    if kind not in ("generated", "imported"):
        return _error("kind must be generated or imported", brand=brand)
    # Workspace visibility: a brand outside the allow-list is not found (no leak),
    # mirroring routes._ensure_brand_visible on the file route.
    allowed = get_allowed_brands()
    if allowed is not None and brand not in allowed:
        return _error("asset not found", brand=brand, asset=asset, file=file)
    root = current_kits_dir().resolve()
    target = (root / brand / kind / asset / file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return _error("asset not found", brand=brand, asset=asset, file=file)
    suffix = target.suffix.lower()
    fmt = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif"}.get(
        suffix
    )
    if fmt is None:
        # Non-raster (e.g. svg): not image content. Point the agent at the path.
        return _error(
            f"{suffix or 'this'} is not raster image content; read it from the path",
            brand=brand,
            asset=asset,
            file=file,
            path=str(target),
        )
    try:
        return Image(data=target.read_bytes(), format=fmt)
    except OSError as exc:  # noqa: BLE001 - boundary guard
        return _error(exc, brand=brand, asset=asset, file=file)


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


# ─────────────────────────── Resource templates ─────────────────────────────
# Per-brand addressable resources: a client can read a brand's book, catalog,
# tokens, or agents by URI without a tool call. Each wraps the same read tool /
# engine the REST surface uses, so a resource read and a tool call agree, and
# each is workspace-scoped exactly like its tool (an out-of-workspace brand reads
# as a structured error, not another tenant's data).


@mcp.resource("brand-ops://brand/{slug}/guidelines")
def brand_guidelines_resource(slug: str) -> str:
    """One brand's full guidelines document (the assembled brand book) as JSON.

    The resource form of get_brand_guidelines / GET /api/brands/{slug}/guidelines.
    """
    return json.dumps(get_brand_guidelines(slug), indent=2)


@mcp.resource("brand-ops://brand/{slug}/assets")
def brand_assets_resource(slug: str) -> str:
    """One brand's generated-asset catalog as JSON.

    The resource form of list_generated_assets for a single brand: the sidecar
    records under brand-kits/<slug>/generated/.
    """
    return json.dumps(list_generated_assets(brand=slug), indent=2)


@mcp.resource("brand-ops://brand/{slug}/tokens")
def brand_tokens_resource(slug: str) -> str:
    """One brand's machine-readable design tokens as JSON.

    The resource form of get_brand_tokens / GET /api/brands/{slug}/tokens.
    """
    return json.dumps(get_brand_tokens(slug), indent=2)


@mcp.resource("brand-ops://brand/{slug}/agents")
def brand_agents_resource(slug: str) -> str:
    """One brand's agent identities as JSON.

    The resource form of list_brand_agents / GET /api/brands/{slug}/agents.
    """
    return json.dumps(list_brand_agents(slug), indent=2)


# ─────────────────────────── Prompts ────────────────────────────────────────
# Reusable, parameterized instruction templates a client can surface as slash
# commands. Each returns a plain instruction string that names the exact tools to
# call, so an agent runs the standard play with the right tools in the right order.


@mcp.prompt(title="Launch a full brand kit")
def launch_full_brand_kit(name: str, description: str = "") -> str:
    """Stand up a brand from a name and generate its full on-brand asset kit."""
    desc = f" Description: {description}." if description.strip() else ""
    return (
        f"Stand up the brand {name!r} end to end.{desc}\n"
        "1. Call intake_brand with the name (and description if given) to draft "
        "the identity and create the brand kit. Note the returned slug.\n"
        "2. Call get_brand_guidelines on the slug and confirm the palette, voice, "
        "and tagline read right; if anything is off, say so before generating.\n"
        "3. Call generate_brand_kit on the slug with only_missing=true to produce "
        "one on-brand asset of every uncovered type (this incurs spend).\n"
        "4. Call get_brand_compliance on the slug and report the pass/warn/fail "
        "rollup. For any fail, propose a concrete fix; do not approve a fail."
    )


@mcp.prompt(title="Audit and remediate brand compliance")
def audit_brand_compliance(brand: str) -> str:
    """Audit a brand's catalog against its rules and remediate the fails."""
    return (
        f"Audit the brand {brand!r} for on-brand compliance and remediate the "
        "fails.\n"
        f"1. Call get_brand_rules on {brand!r} so you know what is being checked.\n"
        f"2. Call get_brand_compliance on {brand!r} to score the whole catalog.\n"
        "3. For each asset with status 'fail', explain the violation in plain "
        "terms. Where a deterministic fix exists (for example a card whose copy "
        "trips a banned-term or em-dash rule), call generate_card to recompose it "
        "on brand, then re-check with check_compliance.\n"
        "4. For a borderline 'warn' a human should accept, call approve_asset to "
        "clear it; never approve a 'fail'.\n"
        "5. Finish with a short before/after summary of the rollup."
    )


@mcp.prompt(title="Stand up a steward agent")
def stand_up_steward_agent(brand: str, name: str = "", role: str = "brand steward") -> str:
    """Create an on-brand steward agent for a brand and give it an avatar."""
    chosen = name.strip() or f"the {brand} steward"
    return (
        f"Stand up an on-brand steward agent for the brand {brand!r}.\n"
        f"1. Call create_agent with brand={brand!r}, name={chosen!r}, and "
        f"role={role!r}. It inherits the brand voice and palette by construction.\n"
        "2. Call generate_agent_variants on the new agent to produce its full "
        "on-brand avatar state set (primary, thinking, success, alert); this "
        "incurs spend. Use generate_agent_avatar instead if you only need the "
        "primary.\n"
        "3. Optionally call list_voices and set_agent_voice to give the agent a "
        "spoken voice.\n"
        "4. Call get_agent_manifest on the agent and confirm the avatar compliance "
        "verdict reads on brand; report it."
    )


# ─────────────────────────── Entrypoint ─────────────────────────────────────


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
