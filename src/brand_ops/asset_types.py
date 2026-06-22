"""Asset-type registry: output sizing and the default backend per asset type.

Per-asset backend defaults follow the research. Logos and icons want clean
transparency or true vector, so they default to backends that do that well.
Text-heavy or composed assets default to the strongest text and composition
backend. Every default is overridable per call with --backend.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssetSpec:
    key: str
    label: str
    size: tuple[int, int]  # width, height in pixels (square assets repeat the value)
    default_backend: str
    transparent: bool = False
    notes: str = ""


ASSET_TYPES: dict[str, AssetSpec] = {
    "logo": AssetSpec(
        key="logo",
        label="Logo",
        size=(2048, 2048),
        default_backend="openai",
        transparent=True,
        notes=(
            "Square mark. OpenAI for clean alpha; vectorize to SVG later. "
            "Recraft is an opt-in provider for native SVG."
        ),
    ),
    "app_icon": AssetSpec(
        key="app_icon",
        label="App icon",
        size=(1024, 1024),
        default_backend="openai",
        transparent=True,
        notes="Must read at 32px. OpenAI gives native transparency.",
    ),
    "launcher_tile": AssetSpec(
        key="launcher_tile",
        label="Launcher tile",
        size=(512, 512),
        default_backend="gemini",
        transparent=False,
        notes=(
            "Square app tile for the Ops-Center launcher (and similar app grids). "
            "A filled, brand-colored tile with the mark centered, legible at ~64px. "
            "Composed deterministically from the brand mark + palette (cards.py), so "
            "it is on brand and pixel-identical without spending on a backend."
        ),
    ),
    "og_image": AssetSpec(
        key="og_image",
        label="OG share image",
        size=(1200, 630),
        default_backend="gemini",
        notes="Text heavy. Gemini has the best legible text.",
    ),
    "podcast_cover": AssetSpec(
        key="podcast_cover",
        label="Podcast cover",
        size=(3000, 3000),
        default_backend="gemini",
    ),
    "social_card": AssetSpec(
        key="social_card",
        label="Social card",
        size=(1080, 1080),
        default_backend="gemini",
    ),
    "avatar": AssetSpec(
        key="avatar",
        label="Avatar",
        size=(1024, 1024),
        default_backend="gemini",
        transparent=False,
        notes="Profile image. Gemini holds a person or character consistent from references.",
    ),
}


def get_asset_spec(asset_type: str) -> AssetSpec:
    try:
        return ASSET_TYPES[asset_type]
    except KeyError as exc:
        known = ", ".join(sorted(ASSET_TYPES))
        raise KeyError(f"unknown asset type {asset_type!r}. Known types: {known}") from exc
