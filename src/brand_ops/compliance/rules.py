"""The rules manifest projected from a brand: what "on brand" means, as data.

``build_rules`` turns a loaded :class:`~brand_ops.models.Brand` into the
deterministic rule set the evaluator scores against: the allowed color palette,
the tolerance and gradient used by the pixel check, the banned and required
copy terms, the voice tone, and the manual (human-judged) rules that the
checker records but does not score. Pure and deterministic.
"""
from __future__ import annotations

from ..models import Brand

# Euclidean RGB distance (0..441) within which a pixel color counts as matching
# an allowed color. 28 is generous enough to absorb JPEG/quantization drift and
# anti-aliasing while still flagging a clearly foreign hue.
COLOR_TOLERANCE = 28

# Manual rules are recorded on every brand's manifest but never auto-scored:
# they need a human eye (clear space, minimum size). They travel with the rules
# so a reviewer or an agent UI can surface the full checklist in one place.
_MANUAL_RULES: list[dict] = [
    {"key": "logo_clear_space", "note": "clear space >= 1x mark height"},
    {"key": "logo_min_size", "note": "min 24px"},
]


def build_rules(brand: Brand) -> dict:
    """Project a brand into a rules manifest matching the BrandRules contract.

    Returns a dict with:

    * ``allowed_colors``: every hex the brand defines, in slot order
      (primary, accent, background, foreground, muted) — only those present.
    * ``color_tolerance``: the RGB match radius used by the color check.
    * ``gradient``: the ``[primary, accent]`` endpoints the color check treats
      as an on-brand gradient axis.
    * ``banned_terms``: the brand's forbidden phrases.
    * ``voice_tone``: the brand's tone keywords.
    * ``required_terms``: empty by default (no required copy yet).
    * ``manual_rules``: the human-judged checklist (recorded, not auto-scored).
    """
    colors = brand.visual.colors
    allowed_colors = [
        value
        for value in (
            colors.primary,
            colors.accent,
            colors.background,
            colors.foreground,
            colors.muted,
        )
        if value
    ]

    return {
        "brand": brand.slug,
        "allowed_colors": allowed_colors,
        "color_tolerance": COLOR_TOLERANCE,
        "gradient": [colors.primary, colors.accent],
        "banned_terms": list(brand.voice.forbidden_phrases),
        "voice_tone": list(brand.voice.tone),
        "required_terms": [],
        "manual_rules": [dict(rule) for rule in _MANUAL_RULES],
    }
