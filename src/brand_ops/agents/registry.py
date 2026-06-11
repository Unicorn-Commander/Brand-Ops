"""Brigade registration manifest for the Brand-Ops agents.

Brand-Ops owns its agents and registers them with Unicorn Brigade so the hub can
discover, gate, and route to them. Each AgentSpec carries the metadata Brigade
needs: a stable name, a human description, a JSON-schema input contract, the
auth scopes the caller must hold, and the minimum suite tier.

This module is pure data plus a small manifest builder. It imports the agent
classes only to bind the runtime class to each spec; building the manifest does
not construct an agent or touch the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .asset_pack import AssetPackAgent
from .avatar import AVATAR_VARIANTS, AvatarAgent
from .base import Agent
from .brand_intake import BrandIntakeAgent
from .brand_research import BrandResearchAgent
from .critic import BrandCriticAgent
from .director import DirectorAgent
from .refine import RefineAgent
from .tagger import TaggerAgent


@dataclass(frozen=True)
class AgentSpec:
    """Registration metadata for one agent in the Brigade manifest."""

    name: str
    description: str
    agent_cls: type[Agent]
    input_schema: dict[str, Any]
    scopes_required: tuple[str, ...]
    tier_required: str
    model_tier: str = "default"  # "default" (Qwen-first) or "strong" (Claude)
    output_summary: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def manifest_entry(self) -> dict[str, Any]:
        """The JSON-serializable form Brigade ingests for this agent."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "scopes_required": list(self.scopes_required),
            "tier_required": self.tier_required,
            "model_tier": self.model_tier,
            "output_summary": self.output_summary,
            "tags": list(self.tags),
        }


def _string(desc: str) -> dict[str, Any]:
    return {"type": "string", "description": desc}


AGENT_REGISTRY: tuple[AgentSpec, ...] = (
    AgentSpec(
        name=DirectorAgent.name,
        description=DirectorAgent.description,
        agent_cls=DirectorAgent,
        input_schema={
            "type": "object",
            "required": ["message"],
            "properties": {
                "message": _string("The operator's new line to Iris."),
                "brand": _string("Active brand slug in the Studio, if any."),
                "history": {
                    "type": "array",
                    "description": "Recent conversation turns, oldest first.",
                    "items": {
                        "type": "object",
                        "required": ["role", "text"],
                        "properties": {
                            "role": {
                                "type": "string",
                                "enum": ["user", "agent"],
                                "description": "Who spoke: the operator or Iris.",
                            },
                            "text": _string("The turn's plain text."),
                        },
                    },
                },
                "brands_context": {
                    "type": "array",
                    "description": "The operator's brands so Iris knows them by name.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "slug": _string("Brand slug."),
                            "display_name": _string("Brand display name."),
                        },
                    },
                },
            },
        },
        scopes_required=("brandops:generate",),
        tier_required="pro",
        model_tier="default",
        output_summary=(
            "An in-persona reply plus a routed intent (chat, single, pack, or "
            "research) the Studio acts on."
        ),
        tags=("brand", "director"),
    ),
    AgentSpec(
        name=BrandResearchAgent.name,
        description=BrandResearchAgent.description,
        agent_cls=BrandResearchAgent,
        input_schema={
            "type": "object",
            "required": ["company"],
            "properties": {
                "company": _string("Company or brand name to research."),
                "domain": _string("Primary web domain, if known."),
                "depth": {
                    "type": "string",
                    "enum": ["light", "medium", "deep"],
                    "default": "medium",
                    "description": "How hard Brigade should research the topic.",
                },
            },
        },
        scopes_required=("brandops:research",),
        tier_required="pro",
        model_tier="strong",
        output_summary="A structured brand profile plus a draft brand.yaml dict.",
        tags=("brand", "research"),
    ),
    AgentSpec(
        name=BrandIntakeAgent.name,
        description=BrandIntakeAgent.description,
        agent_cls=BrandIntakeAgent,
        input_schema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": _string("The new brand's display name."),
                "description": _string(
                    "What the operator can say about the brand, if anything."
                ),
                "url": _string("A related URL or the brand's live site, if any."),
                "parent_brand": {
                    "type": "object",
                    "description": (
                        "The parent brand's identity (colors, voice, style "
                        "keywords) when drafting a sub-brand, so the draft stays "
                        "consistent with the parent."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["describe", "onboard"],
                    "default": "describe",
                    "description": (
                        "describe drafts from the name and description; onboard "
                        "drafts from the url and name as hints."
                    ),
                },
            },
        },
        scopes_required=("brandops:generate",),
        tier_required="pro",
        model_tier="strong",
        output_summary=(
            "A drafted brand identity (palette, typography, voice, style "
            "keywords, tagline, description, rationale) plus honest notes."
        ),
        tags=("brand", "intake", "create"),
    ),
    AgentSpec(
        name=AssetPackAgent.name,
        description=AssetPackAgent.description,
        agent_cls=AssetPackAgent,
        input_schema={
            "type": "object",
            "required": ["brand", "pack"],
            "properties": {
                "brand": _string("Brand slug to generate assets for."),
                "pack": {
                    "type": "string",
                    "enum": AssetPackAgent.available_packs(),
                    "description": "Named asset pack to plan and generate.",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": True,
                    "description": "Plan and build prompts without calling a backend.",
                },
                "backend": _string("Optional backend override for every item."),
            },
        },
        scopes_required=("brandops:generate",),
        tier_required="starter",
        model_tier="default",
        output_summary="A planned pack with one engine outcome per asset.",
        tags=("brand", "generate", "pack"),
    ),
    AgentSpec(
        name=AvatarAgent.name,
        description=AvatarAgent.description,
        agent_cls=AvatarAgent,
        input_schema={
            "type": "object",
            "required": ["brand"],
            "properties": {
                "brand": _string("Brand slug to generate avatars for."),
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12,
                    "default": 1,
                    "description": "How many avatars to plan.",
                },
                "style": {
                    "type": "string",
                    "enum": list(AVATAR_VARIANTS),
                    "description": "Preferred avatar variant. Free text cycles the catalog.",
                },
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Reference image keys from the brand kit.",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": True,
                    "description": "Plan and build prompts without calling a backend.",
                },
                "backend": _string("Optional backend override for every item."),
            },
        },
        scopes_required=("brandops:generate",),
        tier_required="starter",
        model_tier="default",
        output_summary="A planned avatar set with one engine outcome per avatar.",
        tags=("brand", "generate", "avatar"),
    ),
    AgentSpec(
        name=BrandCriticAgent.name,
        description=BrandCriticAgent.description,
        agent_cls=BrandCriticAgent,
        input_schema={
            "type": "object",
            "required": ["asset", "brand_profile"],
            "properties": {
                "asset": {
                    "type": "object",
                    "description": "Asset metadata: prompt, asset_type, variant, paths.",
                },
                "brand_profile": {
                    "type": "object",
                    "description": "Brand profile to judge the asset against.",
                },
            },
        },
        scopes_required=("brandops:critique",),
        tier_required="pro",
        model_tier="strong",
        output_summary="An on-brand judgment, a score, and concrete prompt tweaks.",
        tags=("brand", "critique"),
    ),
    AgentSpec(
        name=TaggerAgent.name,
        description=TaggerAgent.description,
        agent_cls=TaggerAgent,
        input_schema={
            "type": "object",
            "required": ["asset"],
            "properties": {
                "asset": {
                    "type": "object",
                    "description": (
                        "Asset metadata: asset_type (current), brand, filename "
                        "or source, optional description, optional image_url."
                    ),
                },
            },
        },
        scopes_required=("brandops:curate",),
        tier_required="starter",
        model_tier="default",
        output_summary="A corrected asset_type, descriptive tags, and a brand-match judgment.",
        tags=("brand", "curate", "tag"),
    ),
    AgentSpec(
        name=RefineAgent.name,
        description=RefineAgent.description,
        agent_cls=RefineAgent,
        input_schema={
            "type": "object",
            "required": ["asset", "critique"],
            "properties": {
                "asset": {
                    "type": "object",
                    "description": "Asset metadata whose prompt is the base to refine.",
                },
                "critique": {
                    "type": "object",
                    "description": "BrandCriticAgent result; its prompt_tweaks drive refinement.",
                },
                "smooth": {
                    "type": "boolean",
                    "default": True,
                    "description": "Ask the model to rewrite the merge into natural language.",
                },
            },
        },
        scopes_required=("brandops:critique",),
        tier_required="pro",
        model_tier="default",
        output_summary="A refined prompt with the list of tweaks applied.",
        tags=("brand", "critique", "refine"),
    ),
)


def build_manifest() -> dict[str, Any]:
    """Build the full Brigade federation manifest for Brand-Ops agents."""
    return {
        "app": "brand-ops",
        "agents": [spec.manifest_entry() for spec in AGENT_REGISTRY],
    }


def get_agent_spec(name: str) -> AgentSpec:
    """Look up a spec by agent name."""
    for spec in AGENT_REGISTRY:
        if spec.name == name:
            return spec
    known = ", ".join(spec.name for spec in AGENT_REGISTRY)
    raise KeyError(f"unknown agent {name!r}. Known agents: {known}")
