"""Pydantic models for a brand definition (the brand.yaml schema).

brand.yaml is the git-tracked authoring format. These same models back the
database import and every surface (CLI, MCP, REST), so a brand reads the same
way whether it comes from a file or from Postgres.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class BrandColors(BaseModel):
    primary: str
    accent: str
    background: str | None = None
    foreground: str | None = None
    muted: str | None = None

    @field_validator("primary", "accent", "background", "foreground", "muted")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _HEX.match(v):
            raise ValueError(f"color must be a hex value like #b66eff, got {v!r}")
        return v


class BrandTypography(BaseModel):
    primary_font: str = "Inter"
    display_font: str = "Inter"
    mono_font: str = "JetBrains Mono"


class BrandVisual(BaseModel):
    colors: BrandColors
    typography: BrandTypography = Field(default_factory=BrandTypography)
    style_keywords: list[str] = Field(default_factory=list)


class BrandVoice(BaseModel):
    tone: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)


class AssetPrompt(BaseModel):
    base: str
    variants: list[str] = Field(default_factory=list)


class BrandMeta(BaseModel):
    slug: str
    display_name: str
    tagline: str | None = None
    parent: str | None = None
    description: str | None = None


class Brand(BaseModel):
    brand: BrandMeta
    visual: BrandVisual
    voice: BrandVoice = Field(default_factory=BrandVoice)
    prompts: dict[str, AssetPrompt] = Field(default_factory=dict)
    reference_images: dict[str, str] = Field(default_factory=dict)

    @property
    def slug(self) -> str:
        return self.brand.slug
