"""Agent identity assets: on-brand avatar + persona card + manifest per agent.

An agent identity belongs to exactly one brand and inherits its voice and visual
language, so any agent a company spins up (a Steward, a Specialist, or their own
custom agent) comes out on-brand by construction. Identities live per brand under
brand-kits/<slug>/agents/<agent-slug>/ as agent.json (+ avatar.png once generated).

This is deliberately a SEPARATE concept from brand ASSETS: agents are not part of
the asset catalog and never affect brand asset coverage. The avatar is generated
through the same backend resolution as everything else (the LiteLLM gateway when
integrated, native backends in a vacuum), and is compliance-checked against the
brand's color rules just like any other on-brand image.
"""
from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import cost, governance
from .atomic_write import atomic_write_bytes, atomic_write_text
from .backends.base import GenRequest
from .brand_loader import (
    current_kits_dir,
    get_current_workspace_id,
    load_brand,
    resolve_character_references,
)
from .compliance.evaluator import check_image_colors
from .compliance.rules import build_rules
from .engine import resolve_image_backend
from .models import Brand

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Agent avatars resolve through the same chain as the 'avatar' asset type.
_AVATAR_DEFAULT_BACKEND = "gemini"
_AVATAR_SIZE = (1024, 1024)

# The avatar STATE set. ``primary`` is the default avatar (the existing
# avatar.png IS primary). The others are the same character/brand wearing a
# different expression, so an agent can show mood (thinking / pleased /
# attentive) while staying on brand by construction. The order here is the order
# the full set is generated in.
STATES: tuple[str, ...] = ("primary", "thinking", "success", "alert")

# Backend-owned prompt modifiers per state, appended to the primary on-brand
# prompt so every variant shares the exact same palette and style language and
# only the expression changes. ``primary`` adds nothing (it is the base prompt).
_STATE_MODIFIERS: dict[str, str] = {
    "primary": "",
    "thinking": (
        "The character wears a thoughtful, contemplative expression, eyes "
        "looking slightly up as if pondering."
    ),
    "success": "The character wears a pleased and confident expression.",
    "alert": "The character wears an alert and attentive expression.",
}


def _variant_filename(state: str) -> str:
    """The on-disk filename for a state's avatar (primary -> avatar.png)."""
    return "avatar.png" if state == "primary" else f"avatar-{state}.png"


# The avatar derivative sizes (square px) the API can serve on the fly. Resizing
# is done in-memory from the source PNG, so nothing extra is stored on disk.
AVATAR_SIZES: tuple[int, ...] = (32, 64, 128, 256, 512)


def governance_status(compliance: dict | None) -> str:
    """Project an avatar's compliance verdict into a governance bucket.

    One place both the per-agent ``governance_status`` and the team rollup derive
    from, so a card and the team view can never disagree. A passing, warning, or
    skipped (e.g. a vector that can't be pixel-checked) avatar is ``on_brand``; a
    ``fail`` is ``needs_review``; and an agent with no avatar/compliance yet is
    ``unrated``.
    """
    if not isinstance(compliance, dict):
        return "unrated"
    status = compliance.get("status")
    if status == "fail":
        return "needs_review"
    if status in ("pass", "warn", "skipped"):
        return "on_brand"
    return "unrated"


class AgentIdentityError(Exception):
    """A bad agent-identity request (invalid slug, missing agent, etc.)."""


def slugify(name: str) -> str:
    """Derive an agent slug from a display name (lowercase, hyphenated)."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s


def _agents_root(brand_slug: str, kits_dir: Path | None) -> Path:
    root = kits_dir or current_kits_dir()
    return root / brand_slug / "agents"


def _agent_dir(brand_slug: str, agent_slug: str, kits_dir: Path | None) -> Path:
    return _agents_root(brand_slug, kits_dir) / agent_slug


def _voice_tone(brand: Brand) -> list[str]:
    voice = getattr(brand, "voice", None)
    return list(getattr(voice, "tone", []) or [])


def _voice_binding(
    voice_id: str | None, engine: str | None = None, speed: float = 1.0
) -> dict[str, Any] | None:
    """Shape a stored/assigned voice into the agent.json ``voice`` object, or None.

    A bound voice is ``{"voice_id", "engine", "speed"}``: the orator voice name,
    the engine that owns it (resolved from the catalog when not given so the
    manifest is self-describing), and a playback speed. An empty/None ``voice_id``
    clears the binding (returns None), which is how an agent goes back to unvoiced.
    """
    from . import voices

    if not voice_id:
        return None
    try:
        spd = float(speed)
    except (TypeError, ValueError):
        spd = 1.0
    return {
        "voice_id": voice_id,
        "engine": engine or voices.engine_for(voice_id),
        "speed": min(max(spd, 0.5), 2.0),
    }


def _brand_signature_voice(brand: Brand) -> dict[str, Any] | None:
    """The brand's signature voice as an agent voice binding, or None if unset.

    New agents inherit this so a brand's cast sounds consistent by construction,
    the same way they inherit the written voice tone.
    """
    voice = getattr(brand, "voice", None)
    return _voice_binding(
        getattr(voice, "voice_id", None), getattr(voice, "voice_engine", None)
    )


def _variants_urls(
    brand_slug: str, agent_slug: str, data: dict
) -> dict[str, str]:
    """Map each present avatar state to its /api variant URL.

    Reads the on-disk ``variants`` map (state -> filename) and projects it into
    state -> URL. ``primary`` is always included when an avatar exists (it is the
    base ``avatar.png``), even on an older agent.json that predates the variants
    map, so the primary URL is never lost.
    """
    base = f"/api/brands/{brand_slug}/agents/{agent_slug}/variants"
    urls: dict[str, str] = {}
    if data.get("avatar_file"):
        urls["primary"] = f"{base}/primary"
    for state in data.get("variants", {}) or {}:
        if state in _STATE_MODIFIERS:
            urls[state] = f"{base}/{state}"
    return urls


def _identity_dict(
    brand_slug: str, agent_slug: str, data: dict, kits_dir: Path | None
) -> dict[str, Any]:
    """Shape an on-disk agent.json into the API/identity dict, adding avatar_url."""
    avatar_file = data.get("avatar_file")
    avatar_url = (
        f"/api/brands/{brand_slug}/agents/{agent_slug}/avatar" if avatar_file else None
    )
    compliance = data.get("compliance")
    return {
        "slug": agent_slug,
        "brand": brand_slug,
        "name": data.get("name", ""),
        "role": data.get("role", ""),
        "bio": data.get("bio", ""),
        "voice_tone": data.get("voice_tone", []),
        # The bound spoken voice ({voice_id, engine, speed}) or None. Distinct from
        # voice_tone (the written voice): this is the TTS voice Stable/orator speak
        # the agent in.
        "voice": data.get("voice"),
        "avatar_file": avatar_file,
        "avatar_url": avatar_url,
        "variants": _variants_urls(brand_slug, agent_slug, data),
        "compliance": compliance,
        # Precomputed governance bucket so a card can badge without re-deriving
        # the rule from the raw compliance verdict.
        "governance_status": governance_status(compliance),
        "created_at": data.get("created_at"),
    }


def list_agents(brand_slug: str, kits_dir: Path | None = None) -> list[dict[str, Any]]:
    """All agent identities for a brand, newest first."""
    load_brand(brand_slug, kits_dir)  # validates the slug + brand exists
    root = _agents_root(brand_slug, kits_dir)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        meta = d / "agent.json"
        if d.is_dir() and meta.is_file():
            data = json.loads(meta.read_text())
            out.append(_identity_dict(brand_slug, d.name, data, kits_dir))
    out.sort(key=lambda a: a.get("created_at") or "", reverse=True)
    return out


def load_agent(
    brand_slug: str, agent_slug: str, kits_dir: Path | None = None
) -> dict[str, Any]:
    if not _SLUG_RE.match(agent_slug or ""):
        raise AgentIdentityError(f"invalid agent slug {agent_slug!r}")
    meta = _agent_dir(brand_slug, agent_slug, kits_dir) / "agent.json"
    if not meta.is_file():
        raise AgentIdentityError(f"agent {agent_slug!r} not found for brand {brand_slug!r}")
    return _identity_dict(brand_slug, agent_slug, json.loads(meta.read_text()), kits_dir)


def create_agent(
    brand_slug: str,
    name: str,
    role: str,
    bio: str = "",
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Create an agent identity for a brand. Inherits the brand voice tone."""
    brand = load_brand(brand_slug, kits_dir)
    name = (name or "").strip()
    role = (role or "").strip()
    if not name:
        raise AgentIdentityError("agent name is required")
    if not role:
        raise AgentIdentityError("agent role is required")
    agent_slug = slugify(name)
    if not _SLUG_RE.match(agent_slug):
        raise AgentIdentityError(f"name {name!r} does not yield a valid slug")
    d = _agent_dir(brand_slug, agent_slug, kits_dir)
    if (d / "agent.json").exists():
        raise FileExistsError(f"agent {agent_slug!r} already exists for brand {brand_slug!r}")
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "role": role,
        "bio": bio.strip(),
        "voice_tone": _voice_tone(brand),
        # Inherit the brand's signature voice (None when the brand declares none),
        # so a new agent is on-brand in sound as well as look from the start.
        "voice": _brand_signature_voice(brand),
        "avatar_file": None,
        "compliance": None,
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(d / "agent.json", json.dumps(data, indent=2))
    return _identity_dict(brand_slug, agent_slug, data, kits_dir)


def _load_agent_data(
    brand_slug: str, agent_slug: str, kits_dir: Path | None
) -> tuple[Path, dict[str, Any]]:
    """Resolve an agent's agent.json path and parsed data, gated by the brand.

    ``load_brand`` runs first so a brand outside the request's workspace allow-list
    reads as not-found before any agent file is touched (the same tenant guard the
    read path uses). Raises ``AgentIdentityError`` for a bad slug or missing agent.
    """
    load_brand(brand_slug, kits_dir)  # tenant guard + brand-exists
    if not _SLUG_RE.match(agent_slug or ""):
        raise AgentIdentityError(f"invalid agent slug {agent_slug!r}")
    meta = _agent_dir(brand_slug, agent_slug, kits_dir) / "agent.json"
    if not meta.is_file():
        raise AgentIdentityError(
            f"agent {agent_slug!r} not found for brand {brand_slug!r}"
        )
    return meta, json.loads(meta.read_text())


def set_agent_voice(
    brand_slug: str,
    agent_slug: str,
    voice_id: str | None,
    engine: str | None = None,
    speed: float = 1.0,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Bind (or clear) the agent's spoken voice and persist it.

    A non-empty ``voice_id`` must be one of the curated catalog voices, so an
    agent can only be bound to a voice orator actually serves; the engine is
    resolved from the catalog when not supplied. An empty/None ``voice_id`` clears
    the binding. Returns the refreshed identity dict.
    """
    from . import voices

    meta, data = _load_agent_data(brand_slug, agent_slug, kits_dir)
    voice_id = (voice_id or "").strip() or None
    if voice_id is not None and not voices.is_known(voice_id):
        raise AgentIdentityError(f"unknown voice {voice_id!r}")
    data["voice"] = _voice_binding(voice_id, engine, speed)
    atomic_write_text(meta, json.dumps(data, indent=2))
    return _identity_dict(brand_slug, agent_slug, data, kits_dir)


def update_agent(
    brand_slug: str,
    agent_slug: str,
    role: str | None = None,
    bio: str | None = None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Update an agent's persona text (role and/or bio) and persist it.

    Only the fields passed (non-None) change; the avatar, voice, voice tone, and
    compliance are untouched. An empty role is rejected (every agent has a role);
    an empty bio is allowed (it clears the bio).
    """
    meta, data = _load_agent_data(brand_slug, agent_slug, kits_dir)
    if role is not None:
        role = role.strip()
        if not role:
            raise AgentIdentityError("agent role cannot be empty")
        data["role"] = role
    if bio is not None:
        data["bio"] = bio.strip()
    atomic_write_text(meta, json.dumps(data, indent=2))
    return _identity_dict(brand_slug, agent_slug, data, kits_dir)


def _avatar_prompt(brand: Brand, name: str, role: str, bio: str = "") -> str:
    """Build the agent-avatar prompt with references dominant over graphic style.

    When the brand declares ``visual.mascot_style``, that character recipe leads
    and graphic ``style_keywords`` (dark UI, neon glow, etc.) are deliberately
    omitted so they cannot overpower the reference images into an emblem. Without
    a mascot descriptor we still instruct the model to match references first
    and only then apply a short palette + style cue.
    """
    v = brand.visual
    palette = f"{v.colors.primary} and {v.colors.accent}"
    display = brand.brand.display_name
    mascot = (v.mascot_style or "").strip()
    # The agent's own bio IS its art direction — it is the only per-agent
    # description an operator can write. Until 2026-08-14 it was stored,
    # served in manifests, and then dropped on the floor here, so every
    # avatar was generated from name + role + references alone. That is why
    # bios carrying exact hex codes and explicit costume notes changed
    # nothing: the model never saw them.
    character = (bio or "").strip()
    character_clause = f" {character}" if character else ""
    if mascot:
        return (
            f"Create a new character avatar in the EXACT same illustration style as the "
            f"reference images. {mascot} This character is {name}, {role} for the brand "
            f"{display}.{character_clause} Match the reference character design language "
            f"first; brand palette "
            f"({palette}) may tint costume accents only. Square avatar, character centered, "
            f"reads clearly at small sizes. No text and no watermark."
        )
    keywords = ", ".join(list(v.style_keywords)[:4])
    style_cue = (
        f" Secondary style cues (never override the references): {keywords}." if keywords else ""
    )
    return (
        f"Create a new character avatar matching the EXACT illustration style of the "
        f"reference images when provided. This character is {name}, the {role} for the "
        f"brand {display}.{character_clause} Brand palette {palette}.{style_cue} "
        f"Single centered character "
        f"(not a logo emblem) on a simple uncluttered background, high contrast, square "
        f"avatar. No text and no watermark."
    )


def _variant_prompt(brand: Brand, name: str, role: str, state: str, bio: str = "") -> str:
    """The primary on-brand avatar prompt plus this state's expression modifier.

    Every variant reuses the exact primary prompt (same palette and style
    language) and only appends the state's expression, so the whole set is one
    recognizable character on brand by construction. ``primary`` appends nothing.
    """
    base = _avatar_prompt(brand, name, role, bio)
    modifier = _STATE_MODIFIERS.get(state, "")
    return f"{base} {modifier}".rstrip() if modifier else base


def generate_avatar(
    brand_slug: str,
    agent_slug: str,
    settings=None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate an on-brand avatar for the agent, save it, and compliance-check it."""
    from .config import get_settings

    settings = settings or get_settings()
    brand = load_brand(brand_slug, kits_dir)
    meta_path = _agent_dir(brand_slug, agent_slug, kits_dir) / "agent.json"
    if not meta_path.is_file():
        raise AgentIdentityError(f"agent {agent_slug!r} not found for brand {brand_slug!r}")
    data = json.loads(meta_path.read_text())

    _, backend_impl, _ = resolve_image_backend(_AVATAR_DEFAULT_BACKEND, None, settings)
    char_refs = resolve_character_references(brand_slug, brand, kits_dir)
    # Avatar generation is image generation on the shared key, so it goes through
    # the same per-workspace cost gate + metering the engine chokepoint uses
    # (no-op when unscoped or when no cap is set).
    _ws = get_current_workspace_id()
    req = GenRequest(
        prompt=_avatar_prompt(
            brand,
            data.get("name", agent_slug),
            data.get("role", "agent"),
            data.get("bio", ""),
        ),
        width=_AVATAR_SIZE[0],
        height=_AVATAR_SIZE[1],
        count=1,
        reference_images=list(char_refs),
        extra={
            "colors": [brand.visual.colors.primary, brand.visual.colors.accent],
            # Same gateway-side attribution the engine chokepoint sets: the
            # litellm backend forwards this as the OpenAI ``user`` field so the
            # spend lands on THIS workspace instead of only on the app's shared
            # virtual key. Avatars bypass generate_asset, so they were invisible
            # to per-tenant attribution while still costing money. Empty when
            # unscoped, in which case the backend omits the field entirely.
            "user": _ws or "",
        },
    )
    cost.enforce_image_quota_sync(_ws)
    result = backend_impl.generate(req)
    if not result.images:
        raise AgentIdentityError("avatar backend returned no image")

    avatar_path = meta_path.parent / "avatar.png"
    atomic_write_bytes(avatar_path, result.images[0])
    cost.meter_image_spend_sync(
        _ws,
        brand_slug=brand_slug,
        backend=_AVATAR_DEFAULT_BACKEND,
        model=getattr(result, "model", None),
        cost_cents=governance.estimate_image_cost_cents(getattr(result, "cost_cents", None)),
    )

    try:
        compliance = check_image_colors(avatar_path, build_rules(brand))
    except Exception:  # noqa: BLE001 - compliance is advisory, never fail the gen
        compliance = None

    data["avatar_file"] = "avatar.png"
    data["compliance"] = compliance
    atomic_write_text(meta_path, json.dumps(data, indent=2))
    return _identity_dict(brand_slug, agent_slug, data, kits_dir)


def generate_variants(
    brand_slug: str,
    agent_slug: str,
    settings=None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate the full avatar STATE set on-brand and record it on the agent.

    For each state in ``STATES`` (primary, thinking, success, alert) this resolves
    the same image backend ``generate_avatar`` uses, generates a 1024x1024 image
    from the primary prompt plus the state's expression modifier, saves it
    (``primary`` -> avatar.png, the rest -> avatar-<state>.png), and
    compliance-checks each (advisory). It writes a ``variants`` map (state ->
    filename) into agent.json and keeps ``avatar_file``/``compliance`` pointed at
    the primary so the avatar route and manifest keep working. Returns the
    refreshed identity dict (carrying the ``variants`` URL map).
    """
    from .config import get_settings

    settings = settings or get_settings()
    brand = load_brand(brand_slug, kits_dir)
    meta_path = _agent_dir(brand_slug, agent_slug, kits_dir) / "agent.json"
    if not meta_path.is_file():
        raise AgentIdentityError(f"agent {agent_slug!r} not found for brand {brand_slug!r}")
    data = json.loads(meta_path.read_text())

    name = data.get("name", agent_slug)
    role = data.get("role", "agent")
    bio = data.get("bio", "")
    colors = [brand.visual.colors.primary, brand.visual.colors.accent]
    rules = build_rules(brand)
    variants: dict[str, str] = {}
    primary_compliance = data.get("compliance")
    _ws = get_current_workspace_id()

    char_refs = resolve_character_references(brand_slug, brand, kits_dir)
    for state in STATES:
        _, backend_impl, _ = resolve_image_backend(_AVATAR_DEFAULT_BACKEND, None, settings)
        cost.enforce_image_quota_sync(_ws)
        req = GenRequest(
            prompt=_variant_prompt(brand, name, role, state, bio),
            width=_AVATAR_SIZE[0],
            height=_AVATAR_SIZE[1],
            count=1,
            reference_images=list(char_refs),
            # ``user`` attributes this variant's gateway spend to the workspace,
            # exactly as the engine chokepoint and the primary avatar above do.
            # A variant run is STATES-many billable images, so leaving it off
            # made the single largest image spender the least attributable.
            extra={"colors": colors, "user": _ws or ""},
        )
        result = backend_impl.generate(req)
        if not result.images:
            raise AgentIdentityError(f"avatar backend returned no image for state {state!r}")

        filename = _variant_filename(state)
        out_path = meta_path.parent / filename
        atomic_write_bytes(out_path, result.images[0])
        cost.meter_image_spend_sync(
            _ws,
            brand_slug=brand_slug,
            backend=_AVATAR_DEFAULT_BACKEND,
            model=getattr(result, "model", None),
            cost_cents=governance.estimate_image_cost_cents(getattr(result, "cost_cents", None)),
        )

        try:
            compliance = check_image_colors(out_path, rules)
        except Exception:  # noqa: BLE001 - compliance is advisory, never fail the gen
            compliance = None

        variants[state] = filename
        if state == "primary":
            primary_compliance = compliance

    data["avatar_file"] = _variant_filename("primary")
    data["compliance"] = primary_compliance
    data["variants"] = variants
    atomic_write_text(meta_path, json.dumps(data, indent=2))
    return _identity_dict(brand_slug, agent_slug, data, kits_dir)


def avatar_path(
    brand_slug: str, agent_slug: str, kits_dir: Path | None = None
) -> Path | None:
    """Confined path to the agent's avatar, or None if not generated."""
    if not _SLUG_RE.match(agent_slug or ""):
        raise AgentIdentityError(f"invalid agent slug {agent_slug!r}")
    p = _agent_dir(brand_slug, agent_slug, kits_dir) / "avatar.png"
    return p if p.is_file() else None


def variant_path(
    brand_slug: str, agent_slug: str, state: str, kits_dir: Path | None = None
) -> Path | None:
    """Confined path to the agent's avatar for ``state``, or None if absent.

    Validates the slug and that ``state`` is a known avatar state, so an unknown
    or crafted state can never resolve a file. ``primary`` maps to avatar.png; the
    others to avatar-<state>.png.
    """
    if not _SLUG_RE.match(agent_slug or ""):
        raise AgentIdentityError(f"invalid agent slug {agent_slug!r}")
    if state not in _STATE_MODIFIERS:
        raise AgentIdentityError(f"unknown avatar state {state!r}")
    p = _agent_dir(brand_slug, agent_slug, kits_dir) / _variant_filename(state)
    return p if p.is_file() else None


def resize_avatar_png(src: Path, size: int) -> bytes:
    """Downscale a source avatar PNG to a square ``size`` x ``size`` PNG.

    Used by the avatar/variant routes to serve size derivatives on the fly: the
    source is read, converted to RGBA so transparency is preserved, resized with
    a high-quality LANCZOS filter, and re-encoded as PNG bytes. ``size`` must be
    one of ``AVATAR_SIZES``; an unsupported value raises ``AgentIdentityError``
    (the route maps that to a 422). The caller resolves and confines ``src`` and
    guarantees it exists.
    """
    if size not in AVATAR_SIZES:
        raise AgentIdentityError(f"unsupported avatar size {size!r}")
    from PIL import Image

    with Image.open(src) as img:
        rgba = img.convert("RGBA")
        resized = rgba.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
    return buf.getvalue()


def _brand_mark_url(brand: Brand) -> str | None:
    """The /api/assets/file URL for a brand's canonical mark, or None.

    Mirrors ``api.routes._mark_url`` so the steward avatar and the brand list
    resolve the same file the same way; kept here so ``build_team`` is correct on
    its own (the route does not have to backfill the URL) and DRY against the same
    mark fields. The file component is percent-encoded because mark filenames can
    carry spaces or other reserved characters.
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


def build_team(brand_slug: str, kits_dir: Path | None = None) -> dict[str, Any]:
    """The brand's agent team: the Director, the brand Steward, and its agents.

    A per-brand rollup for the team view. ``director`` is the single Director
    persona (Iris) every brand shares; ``steward`` is this brand's dedicated agent
    (named for the brand, its avatar the brand's canonical mark); ``agents`` is the
    list of created agent identities (the same dicts ``list_agents`` returns). The
    ``governance`` block tallies those created agents by their avatar compliance
    bucket — ``on_brand`` (pass/warn/skipped), ``needs_review`` (fail), or
    ``unrated`` (no avatar yet) — so the view can show coverage at a glance.
    """
    brand = load_brand(brand_slug, kits_dir)
    display_name = brand.brand.display_name
    agents = list_agents(brand_slug, kits_dir)

    counts = {"on_brand": 0, "needs_review": 0, "unrated": 0}
    for agent in agents:
        counts[governance_status(agent.get("compliance"))] += 1

    return {
        "brand": brand_slug,
        "brand_display_name": display_name,
        # The Director persona is brand-agnostic and has no per-brand avatar here.
        "director": {
            "name": "Iris",
            "role": "brand director",
            "kind": "director",
            "avatar_url": None,
        },
        # The Steward is the brand's own agent; the brand mark stands in as its
        # avatar until a dedicated one is generated.
        "steward": {
            "name": display_name,
            "role": "brand steward",
            "kind": "steward",
            "avatar_url": _brand_mark_url(brand),
        },
        "agents": agents,
        "governance": {
            "total": len(agents),
            "on_brand": counts["on_brand"],
            "needs_review": counts["needs_review"],
            "unrated": counts["unrated"],
        },
    }


def build_manifest(
    brand_slug: str, agent_slug: str, kits_dir: Path | None = None
) -> dict[str, Any]:
    """A Brigade/suite-consumable identity manifest for the agent."""
    brand = load_brand(brand_slug, kits_dir)
    ident = load_agent(brand_slug, agent_slug, kits_dir)
    variants = ident["variants"]
    return {
        "schema": "brand-ops/agent-identity@2",
        "source": "brand-ops",
        "agent": agent_slug,
        "name": ident["name"],
        "role": ident["role"],
        "bio": ident["bio"],
        "brand": brand_slug,
        "brand_display_name": brand.brand.display_name,
        "avatar_url": ident["avatar_url"],
        "avatars": {
            "primary": variants.get("primary"),
            "variants": {s: u for s, u in variants.items() if s != "primary"},
            # The size derivatives the avatar/variant routes can serve via ?size=.
            "sizes": list(AVATAR_SIZES),
        },
        # The voice block carries both halves of "voice": the written tone and the
        # spoken TTS binding ({voice_id, engine, speed} or None). Stable/orator read
        # ``tts`` to speak the agent; an LLM reads ``tone`` to write as it.
        "voice": {"tone": ident["voice_tone"], "tts": ident.get("voice")},
        "palette": {
            "primary": brand.visual.colors.primary,
            "accent": brand.visual.colors.accent,
        },
        "compliance": ident["compliance"],
    }
