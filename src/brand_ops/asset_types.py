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
