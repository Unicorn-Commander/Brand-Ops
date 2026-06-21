"""Clone-a-brand: stand up a new brand kit seeded from an existing one.

Two shapes, both written as a fresh ``brand-kits/<new_slug>/brand.yaml``:

* ``as_child=True`` writes a minimal brand block — ``{slug, display_name,
  parent: <source>}`` — and nothing else, so the loader's existing parent merge
  supplies the visual, voice, prompts, and reference images from the source at
  read time. The clone is a true sub-brand that tracks the parent.
* ``as_child=False`` resolves the source brand in full (applying any parent
  merge the source itself has) and copies its visual, voice, and prompt
  templates into the new file with ``parent: null``. The clone is a distinct
  brand seeded from the source, free to diverge.

A brand's canonical mark is never copied: a new brand has no logo of its own
yet, so ``mark`` stays unset and its ``mark_url`` is null until one is imported.
Reference images are not copied either — they name files (or absolute paths)
that belong to the source kit, so a copy would dangle; an ``as_child`` clone
inherits the source's references through the merge, and a distinct clone starts
with none.

This is the one clone code path the REST route and the MCP tool both call, so
the human and the agent surfaces can never drift.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .atomic_write import atomic_write_text
from .brand_loader import (
    BrandNotFound,
    allow_brand,
    current_kits_dir,
    get_allowed_brands,
    get_current_workspace_id,
    load_brand,
)
from .models import Brand

# Same slug shape the rest of the kit enforces, matching brand_loader's read
# guard exactly: a single segment of lowercase letters, digits, and hyphens that
# must start alphanumeric (so an all-hyphen or leading-hyphen name cannot be
# created, and every slug written here is one the loader will read back).
# Anything else is rejected before any file is touched.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Default prompt templates written into a brand created from a draft, so the new
# brand is immediately generatable through the same engine every other brand
# uses. These mirror brand-kits/_template/brand.yaml verbatim: the engine fills
# {brand_context}, {style_keywords}, {variant}, {topic}, {platform}, and
# {ref_block} at compose time, so a fresh brand needs no hand-editing to produce
# its first asset. Held here (not read from disk) so create_brand_from_draft is
# pure and testable against any kits_dir.
_DEFAULT_PROMPTS: dict[str, dict[str, object]] = {
    "logo": {
        "base": (
            "Generate a logo for {brand_context}. Style: {style_keywords}. The "
            "logo should be {variant}. Output a clean square composition with a "
            "transparent background where possible. {ref_block}"
        ),
        "variants": ["flat", "monochrome", "color", "app-icon"],
    },
    "app_icon": {
        "base": (
            "Generate an app icon for {brand_context}. {style_keywords}. It must "
            "stay recognizable at small sizes like 32x32 and 64x64. Square "
            "composition, rounded corners, deep contrast. {ref_block}"
        ),
        "variants": ["color", "monochrome", "dark", "light"],
    },
    "og_image": {
        "base": (
            "Generate a 1200x630 share image for {brand_context}. Put the "
            "headline area on the left and the brand mark on the right. Topic: "
            "{topic}. {style_keywords}. {ref_block}"
        ),
        "variants": ["default", "dark", "light"],
    },
    "podcast_cover": {
        "base": (
            "Generate a 3000x3000 square podcast cover for {brand_context}. "
            "Episode topic: {topic}. {style_keywords}. {ref_block}"
        ),
        "variants": ["series", "episode"],
    },
    "social_card": {
        "base": (
            "Generate a square social card for {brand_context}. Topic: {topic}. "
            "Platform: {platform}. {style_keywords}. {ref_block}"
        ),
        "variants": ["instagram", "twitter", "linkedin"],
    },
}


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"invalid slug {slug!r}: use lowercase letters, digits, and hyphens"
        )


def _owning_workspace() -> str | None:
    """The workspace_id to stamp onto a newly written brand.

    When a request is scoped to a workspace (the switcher / token resolved one),
    a brand it creates records that workspace, so the file->DB importer assigns
    it correctly on any later re-run. Unscoped (CLI / stdio MCP / tests) it is
    None, and the importer assigns such a brand to the deploy's bootstrap
    workspace. The DB ``brand`` row the route writes at creation is the
    authority; this is just the file's record of ownership.
    """
    return get_current_workspace_id()


def _assert_slug_available(slug: str, root: Path) -> None:
    """Raise ``FileExistsError`` if ``slug`` is taken, without leaking a tenant.

    Brand storage is a single on-disk namespace (``brand-kits/<slug>/``);
    per-workspace storage is a deferred refactor. Until then a create must not
    become an existence oracle across that shared namespace: a scoped caller
    (``allowed`` is not None) who is not a member of the brand's workspace gets a
    generic "unavailable" that does not confirm the brand exists anywhere. The
    caller's own slug, or an unscoped CLI/stdio create, gets the precise "already
    exists". Neither message ever includes the filesystem path.
    """
    if not (root / slug).exists():
        return
    allowed = get_allowed_brands()
    if allowed is not None and slug not in allowed:
        raise FileExistsError("that brand name is unavailable")
    raise FileExistsError(f"brand {slug!r} already exists")


def clone_brand(
    source_slug: str,
    new_slug: str,
    display_name: str,
    as_child: bool = True,
    kits_dir: Path | None = None,
) -> Brand:
    """Clone ``source_slug`` into a new ``brand-kits/<new_slug>/brand.yaml``.

    Args:
        source_slug: The existing brand to seed from.
        new_slug: The new brand's slug (``^[a-z0-9-]+$``).
        display_name: The new brand's human-readable name.
        as_child: When True (default), write a minimal child block that inherits
            the source via the loader merge. When False, copy the source's
            resolved visual, voice, and prompts in with ``parent: null``.
        kits_dir: Override the brand-kits root (used by tests).

    Returns:
        The loaded, validated :class:`Brand` for the newly written clone.

    Raises:
        ValueError: ``new_slug`` is not a valid slug.
        FileNotFoundError / BrandNotFound: ``source_slug`` does not exist.
        FileExistsError: ``new_slug`` already exists.
    """
    _validate_slug(new_slug)

    root = kits_dir or current_kits_dir()

    # Resolve the source first: this both validates the source exists (raising
    # BrandNotFound, a FileNotFoundError subclass is not used here so the route
    # maps it explicitly) and gives us the fully merged brand for a distinct
    # clone. A missing source must fail before we create any directory.
    source = load_brand(source_slug, kits_dir=root)

    dest_dir = root / new_slug
    _assert_slug_available(new_slug, root)

    if as_child:
        # Minimal block. No visual/voice/prompts/reference_images keys, so the
        # loader's parent merge supplies all of them from the source at read
        # time. The mark is intentionally absent (a new brand has no logo yet).
        doc: dict = {
            "brand": {
                "slug": new_slug,
                "display_name": display_name,
                "parent": source_slug,
                "workspace_id": _owning_workspace(),
            }
        }
    else:
        # A distinct brand seeded from the source's resolved identity. Copy the
        # visual and voice verbatim and the prompt templates so the clone is
        # immediately usable, with no parent. The mark is never copied and the
        # source's reference images (which point at the source's own files) are
        # left out so nothing dangles.
        visual = source.visual.model_dump(mode="json", exclude_none=True)
        voice = source.voice.model_dump(mode="json", exclude_none=True)
        prompts = {
            name: prompt.model_dump(mode="json", exclude_none=True)
            for name, prompt in source.prompts.items()
        }
        doc = {
            "brand": {
                "slug": new_slug,
                "display_name": display_name,
                "parent": None,
                "workspace_id": _owning_workspace(),
            },
            "visual": visual,
            "voice": voice,
        }
        if prompts:
            doc["prompts"] = prompts

    dest_dir.mkdir(parents=True)
    brand_yaml = dest_dir / "brand.yaml"
    atomic_write_text(
        brand_yaml,
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False),
    )

    # Make the just-created brand visible for the rest of this (scoped) request,
    # so the read-back below does not 404 under the workspace allow-list. No-op
    # when unscoped (CLI / tests).
    allow_brand(new_slug)

    # Load through the same path every surface uses, so the returned Brand is
    # exactly what GET /api/brands/<new_slug> will serve (and the as_child merge
    # is exercised here, not just asserted downstream).
    return load_brand(new_slug, kits_dir=root)


def create_brand_from_draft(
    slug: str,
    draft: dict,
    parent: str | None = None,
    kits_dir: Path | None = None,
) -> Brand:
    """Write a new ``brand-kits/<slug>/brand.yaml`` from a drafted identity.

    This is the writer behind real brand intake: the Director drafts an identity
    (see ``agents.brand_intake.BrandIntakeAgent.draft_identity``) and this turns
    that draft into a complete, valid brand.yaml the rest of Brand-Ops authors
    from. It shares the slug validation and the duplicate-slug guard with
    ``clone_brand`` so the create and clone paths can never drift, and it writes
    the same default prompt templates a fresh kit carries so the new brand is
    immediately generatable.

    The ``draft`` is the agent's contract:
    ``{palette:{primary,accent,background,foreground,muted}, typography:
    {primary_font,display_font,mono_font}, voice_tone:[...], style_keywords:[...],
    tagline, description, rationale}``. Only the keys this writer needs are read;
    ``rationale`` is for the conversation, not the file. Every color is validated
    by the loaded ``Brand`` (the BrandColors hex rule), so a bad hex raises a
    ValueError from the load rather than writing an invalid file. The canonical
    mark is never set (a new brand has no logo yet) and ``reference_images`` is
    empty, so the brand's ``mark_url`` is null until one is imported.

    Args:
        slug: The new brand's slug (``^[a-z0-9-]+$``).
        draft: The drafted identity dict described above.
        parent: Optional parent brand slug for a sub-brand. Recorded on the
            brand block; the loader's parent merge applies at read time.
        kits_dir: Override the brand-kits root (used by tests).

    Returns:
        The loaded, validated :class:`Brand` for the newly written brand.

    Raises:
        ValueError: ``slug`` is invalid, or a drafted color is not a valid hex
            (surfaced from the validating load).
        FileExistsError: ``slug`` already exists.
    """
    _validate_slug(slug)

    root = kits_dir or current_kits_dir()
    dest_dir = root / slug
    _assert_slug_available(slug, root)

    palette = draft.get("palette") if isinstance(draft, dict) else None
    palette = palette if isinstance(palette, dict) else {}
    typography = draft.get("typography") if isinstance(draft, dict) else None
    typography = typography if isinstance(typography, dict) else {}

    # The colors block: primary and accent are required by the schema; the rest
    # are written when the draft supplies them. The validating load enforces the
    # hex rule, so an invalid color here surfaces as a ValueError (a clean 422 at
    # the route) rather than a written-but-broken file.
    colors: dict[str, object] = {}
    for role in ("primary", "accent", "background", "foreground", "muted"):
        value = palette.get(role)
        if isinstance(value, str) and value.strip():
            colors[role] = value.strip()

    visual: dict[str, object] = {"colors": colors}
    typo: dict[str, object] = {}
    for key in ("primary_font", "display_font", "mono_font"):
        font = typography.get(key)
        if isinstance(font, str) and font.strip():
            typo[key] = font.strip()
    if typo:
        visual["typography"] = typo
    style_keywords = _as_str_list(draft.get("style_keywords") if isinstance(draft, dict) else None)
    if style_keywords:
        visual["style_keywords"] = style_keywords

    voice: dict[str, object] = {}
    tone = _as_str_list(draft.get("voice_tone") if isinstance(draft, dict) else None)
    if tone:
        voice["tone"] = tone

    brand_block: dict[str, object] = {
        "slug": slug,
        "display_name": _display_name(draft) or slug,
        "parent": parent,
        "workspace_id": _owning_workspace(),
    }
    tagline = _clean(draft.get("tagline") if isinstance(draft, dict) else None)
    if tagline:
        brand_block["tagline"] = tagline
    description = _clean(draft.get("description") if isinstance(draft, dict) else None)
    if description:
        brand_block["description"] = description

    doc: dict[str, object] = {
        "brand": brand_block,
        "visual": visual,
        # A copy so a later mutation of the module constant cannot leak into a
        # written file (yaml.safe_dump reads it now, but the copy keeps the
        # default templates immutable across calls).
        "prompts": {name: dict(prompt) for name, prompt in _DEFAULT_PROMPTS.items()},
        # A new brand has no reference images of its own; composition mode stays
        # off until the operator imports some. Empty, never the source's paths.
        "reference_images": {},
    }
    if voice:
        doc["voice"] = voice

    dest_dir.mkdir(parents=True)
    brand_yaml = dest_dir / "brand.yaml"
    atomic_write_text(
        brand_yaml,
        yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=False),
    )

    # Make the just-created brand visible for the rest of this (scoped) request
    # so the validating read-back below does not 404 under the allow-list.
    allow_brand(slug)

    # Load through the same path every surface uses. This is also where an
    # invalid drafted color is caught: load_brand validates the Brand, so a bad
    # hex raises ValueError here (the route maps it to 422). The directory was
    # already created, so the route's cleanup or the operator removes it; the
    # agent's hex validation makes this practically unreachable from intake.
    return load_brand(slug, kits_dir=root)


def _display_name(draft: dict) -> str:
    """Pull a display name from a draft, tolerating absence."""
    if isinstance(draft, dict):
        value = draft.get("display_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clean(value: object) -> str:
    """A trimmed non-empty string, or empty string."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _as_str_list(value: object) -> list[str]:
    """Coerce a list (or comma string) into clean, unique strings preserving order."""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = [str(v) for v in value]
    else:
        return []
    seen: list[str] = []
    for item in items:
        token = str(item).strip()
        if token and token not in seen:
            seen.append(token)
    return seen


__all__ = ["clone_brand", "create_brand_from_draft", "BrandNotFound"]
