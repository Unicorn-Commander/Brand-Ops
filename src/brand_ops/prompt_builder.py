"""Compose the final image prompt from a brand plus an asset request.

Tokens in the brand's prompt template are replaced here:
  {brand_context}   display name, tagline, description
  {style_keywords}  the brand's visual.style_keywords, comma joined
  {ref_block}       an instruction to match the supplied reference images
  {variant} {topic} {platform}  caller supplied
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .asset_types import AssetSpec, get_asset_spec
from .lint import check_copy
from .models import AssetPrompt, Brand

# Shared default templates. A brand.yaml only needs a prompts block when it wants
# to override one of these. Adding a new asset type here makes it work for every
# brand at once. Keep all copy free of em-dashes and marketing-speak.
DEFAULT_PROMPTS: dict[str, AssetPrompt] = {
    "logo": AssetPrompt(
        base=(
            "Generate a logo for {brand_context}. Style: {style_keywords}. The logo "
            "should be {variant}. Output a clean square composition with a transparent "
            "background where possible. {ref_block}"
        ),
        variants=["flat", "monochrome", "color", "app-icon"],
    ),
    "app_icon": AssetPrompt(
        base=(
            "Generate an app icon for {brand_context}. {style_keywords}. It must stay "
            "recognizable at small sizes like 32x32 and 64x64. Square composition, "
            "rounded corners, deep contrast. {ref_block}"
        ),
        variants=["color", "monochrome", "dark", "light"],
    ),
    "og_image": AssetPrompt(
        base=(
            "Generate a 1200x630 share image for {brand_context}. Put the headline area "
            "on the left and the brand mark on the right. Topic: {topic}. "
            "{style_keywords}. {ref_block}"
        ),
        variants=["default", "dark", "light"],
    ),
    "podcast_cover": AssetPrompt(
        base=(
            "Generate a 3000x3000 square podcast cover for {brand_context}. Episode "
            "topic: {topic}. {style_keywords}. {ref_block}"
        ),
        variants=["series", "episode"],
    ),
    "social_card": AssetPrompt(
        base=(
            "Generate a square social card for {brand_context}. Topic: {topic}. "
            "Platform: {platform}. {style_keywords}. {ref_block}"
        ),
        variants=["instagram", "twitter", "linkedin"],
    ),
    "avatar": AssetPrompt(
        base=(
            "Generate an avatar for {brand_context}. {style_keywords}. A clean, "
            "recognizable {variant} avatar that reads well as a small circular profile "
            "image, with the subject centered and a simple uncluttered background. "
            "{ref_block}"
        ),
        variants=["portrait", "headshot", "mascot", "abstract"],
    ),
}


@dataclass
class BuiltPrompt:
    text: str
    asset_type: str
    variant: str | None
    backend: str
    size: tuple[int, int]
    transparent: bool
    reference_images: list[Path]
    warnings: list[str]


def _brand_context(brand: Brand) -> str:
    meta = brand.brand
    parts = [meta.display_name]
    if meta.tagline:
        parts.append(meta.tagline)
    if meta.description:
        parts.append(" ".join(meta.description.split()))
    # Each part may already end in a period; strip trailing dots so neither the
    # join nor the template's own punctuation produces "..".
    cleaned = [p.strip().rstrip(".").strip() for p in parts if p and p.strip()]
    return ". ".join(cleaned)


def _ref_block(refs: list[Path]) -> str:
    if not refs:
        return ""
    names = ", ".join(p.name for p in refs)
    return (
        f"Use the supplied reference image(s) to stay on brand: {names}. "
        "Match their color, proportion, and overall feel."
    )


def _palette_clause(brand: Brand, transparent: bool) -> str:
    """State the brand's exact hex so the model renders on-brand colors.

    Background is included only for non-transparent assets: a transparent asset
    wants a clean field to cut out, not a painted background color.
    """
    colors = brand.visual.colors
    parts: list[str] = []
    if colors.primary:
        parts.append(f"primary {colors.primary}")
    if colors.accent:
        parts.append(f"accent {colors.accent}")
    if not transparent and getattr(colors, "background", None):
        parts.append(f"background {colors.background}")
    if not parts:
        return ""
    return "Use the exact brand colors: " + ", ".join(parts) + "."


def _transparency_clause(transparent: bool) -> str:
    """For transparent assets, ask for a flat uniform field the engine can cut.

    The gateway backend cannot return a transparent PNG, so the mark is rendered
    on a flat background and the background is knocked out after generation. A
    busy or gradient background would defeat that, so we steer away from one.
    """
    if not transparent:
        return ""
    return (
        "Center the mark with generous margin on a single plain solid flat-color "
        "background (one uniform fill color) so the background can be cleanly cut "
        "out to transparency afterwards. Do not add a scene, photo, or gradient "
        "behind the mark, and do NOT draw a checkerboard or transparency-grid "
        "pattern: render an actual solid fill color."
    )


def build_prompt(
    brand: Brand,
    asset_type: str,
    *,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    reference_images: list[Path] | None = None,
    backend: str | None = None,
) -> BuiltPrompt:
    spec: AssetSpec = get_asset_spec(asset_type)
    template = brand.prompts.get(asset_type) or DEFAULT_PROMPTS.get(asset_type)
    if template is None:
        known = ", ".join(sorted(set(brand.prompts) | set(DEFAULT_PROMPTS))) or "(none)"
        raise KeyError(f"no prompt template for asset {asset_type!r}. Known: {known}")

    if variant and template.variants and variant not in template.variants:
        allowed = ", ".join(template.variants)
        raise ValueError(f"variant {variant!r} not in {asset_type} variants: {allowed}")

    refs = reference_images or []
    substitutions = {
        "brand_context": _brand_context(brand),
        "style_keywords": ", ".join(brand.visual.style_keywords),
        "ref_block": _ref_block(refs),
        "variant": variant or "",
        "topic": topic or "",
        "platform": platform or "",
    }
    text = template.base
    for token, value in substitutions.items():
        text = text.replace("{" + token + "}", value)
    # Append exact-color guidance, and for transparent assets a clean-background
    # instruction, so the model renders on-brand and the post-gen knockout has a
    # uniform field to remove.
    clauses = (
        _palette_clause(brand, spec.transparent),
        _transparency_clause(spec.transparent),
    )
    for clause in clauses:
        if clause:
            text = f"{text} {clause}"
    text = " ".join(text.split())

    warnings = check_copy(text, brand.voice.forbidden_phrases)

    return BuiltPrompt(
        text=text,
        asset_type=asset_type,
        variant=variant,
        backend=backend or spec.default_backend,
        size=spec.size,
        transparent=spec.transparent,
        reference_images=refs,
        warnings=warnings,
    )
