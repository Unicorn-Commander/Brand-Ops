"""Brand-Ops agents, owned in-module and registered with Unicorn Brigade.

Per the suite model each app owns its agents next to the engine they drive, so
the agent path and the human path call the same generate_asset and can never
drift. The agents reach an OpenAI-compatible LLM gateway and delegate heavy web
research to Brigade's deep-research agent through the shared BrigadeClient.

This package is the pilot agent runtime. base.Agent is kept deliberately small
because it is the seed of a suite-wide agent SDK that will be extracted later.
"""
from __future__ import annotations

from .asset_pack import AssetPackAgent
from .avatar import AvatarAgent
from .base import Agent
from .brand_research import BrandResearchAgent
from .critic import BrandCriticAgent
from .registry import AGENT_REGISTRY, AgentSpec

__all__ = [
    "AGENT_REGISTRY",
    "Agent",
    "AgentSpec",
    "AssetPackAgent",
    "AvatarAgent",
    "BrandCriticAgent",
    "BrandResearchAgent",
]
