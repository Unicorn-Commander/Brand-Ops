"""Agent import checks and pure-logic unit tests.

No test here touches a real LLM or the network. The agents are import-checked,
and the deterministic planning logic (AssetPackAgent, AvatarAgent, the registry
manifest) is unit-tested directly. Where an agent is exercised through run, a
stub Brigade client stands in for the gateway so nothing leaves the process.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

# Import every agent and the brigade client. This is the import-check half of
# the assignment: if any module is broken, collection fails here.
from brand_ops.agents import (
    AGENT_REGISTRY,
    Agent,
    AssetPackAgent,
    AvatarAgent,
    BrandCriticAgent,
    BrandResearchAgent,
)
from brand_ops.agents.asset_pack import PACKS, PackItem
from brand_ops.agents.avatar import AVATAR_VARIANTS
from brand_ops.agents.registry import build_manifest, get_agent_spec
from brand_ops.brigade import BrigadeClient, BrigadeUnavailable
from brand_ops.brigade.client import _first_message_content
from brand_ops.config import Settings


class _StubBrigade:
    """A stand-in Brigade client. Records calls; never touches the network."""

    def __init__(self, chat_reply: str | None = None) -> None:
        self.chat_reply = chat_reply
        self.research_calls: list[tuple[str, str]] = []
        self.chat_calls: list[dict[str, Any]] = []

    async def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        self.research_calls.append((topic, depth))
        return {"summary": "stub research"}

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        temperature: float = 0.4,
        response_json: bool = False,
    ) -> str | None:
        self.chat_calls.append(
            {"messages": messages, "model": model, "response_json": response_json}
        )
        return self.chat_reply


# -- AssetPackAgent pure planning -------------------------------------------


def test_plan_pack_core_is_deterministic() -> None:
    plan = AssetPackAgent.plan_pack("core")
    assert plan == [
        PackItem("logo", "color"),
        PackItem("logo", "monochrome"),
        PackItem("app_icon", "color"),
        PackItem("og_image", "default"),
    ]


def test_plan_pack_is_case_insensitive_and_trims() -> None:
    assert AssetPackAgent.plan_pack("  CORE ") == AssetPackAgent.plan_pack("core")


def test_plan_pack_returns_pack_items() -> None:
    plan = AssetPackAgent.plan_pack("social")
    assert plan, "social pack should not be empty"
    for item in plan:
        assert isinstance(item, PackItem)
        assert isinstance(item.asset_type, str) and item.asset_type
        # variant is either a string or None
        assert item.variant is None or isinstance(item.variant, str)


def test_plan_pack_covers_every_named_pack() -> None:
    # Every declared pack must plan without error and be non-empty.
    for name in PACKS:
        plan = AssetPackAgent.plan_pack(name)
        assert plan, f"pack {name} planned empty"


def test_plan_pack_unknown_raises_keyerror_with_known_list() -> None:
    with pytest.raises(KeyError) as exc:
        AssetPackAgent.plan_pack("does-not-exist")
    message = str(exc.value)
    assert "does-not-exist" in message
    assert "core" in message  # the known list is shown to the caller


def test_available_packs_matches_pack_keys() -> None:
    assert AssetPackAgent.available_packs() == sorted(PACKS)


def test_plan_pack_is_pure_no_network_no_llm() -> None:
    # The planning method is a staticmethod and must not require an agent
    # instance, settings, or a gateway. Calling it on the class is the proof.
    plan = AssetPackAgent.plan_pack("launch")
    assert len(plan) == len(PACKS["launch"])


def test_asset_pack_run_dry_run_with_stub_does_not_hit_network(tmp_path) -> None:
    # Exercise run with a stub gateway and a brand that does not exist, in
    # dry_run. The engine call fails per item (no such brand) and is captured,
    # proving run never aborts and never reaches a backend or the LLM.
    agent = AssetPackAgent(brigade=_StubBrigade(), settings=Settings())
    result = asyncio.run(agent.run({"brand": "no-such-brand", "pack": "core", "dry_run": True}))
    assert result["status"] == "ok"
    assert result["pack"] == "core"
    assert result["count"] == len(PACKS["core"])
    # Each item recorded an outcome; with a missing brand each is an error entry.
    assert len(result["items"]) == len(PACKS["core"])
    for entry in result["items"]:
        assert entry["status"] in {"planned", "error"}


def test_asset_pack_run_requires_brand_and_pack() -> None:
    agent = AssetPackAgent(brigade=_StubBrigade(), settings=Settings())
    missing_brand = asyncio.run(agent.run({"pack": "core"}))
    assert missing_brand["status"] == "error"
    missing_pack = asyncio.run(agent.run({"brand": "acme"}))
    assert missing_pack["status"] == "error"


def test_asset_pack_run_unknown_pack_is_clean_error() -> None:
    agent = AssetPackAgent(brigade=_StubBrigade(), settings=Settings())
    result = asyncio.run(agent.run({"brand": "acme", "pack": "nope"}))
    assert result["status"] == "error"
    assert "nope" in result["error"]


# -- AvatarAgent pure planning ----------------------------------------------


def test_plan_avatars_known_style_repeats() -> None:
    assert AvatarAgent.plan_avatars(3, "headshot") == ["headshot", "headshot", "headshot"]


def test_plan_avatars_unknown_style_cycles_known_variants() -> None:
    plan = AvatarAgent.plan_avatars(5, "something-else")
    assert len(plan) == 5
    assert set(plan) <= set(AVATAR_VARIANTS)
    assert plan[0] == "portrait"  # stable default first


def test_plan_avatars_clamps_count() -> None:
    assert len(AvatarAgent.plan_avatars(0)) == 1
    assert len(AvatarAgent.plan_avatars(999)) == 12


def test_plan_avatars_variants_are_engine_valid() -> None:
    # Every planned variant must be one the avatar template accepts.
    for variant in AvatarAgent.plan_avatars(8, None):
        assert variant in AVATAR_VARIANTS


# -- Registry / manifest (pure) ---------------------------------------------


def test_registry_lists_every_agent() -> None:
    names = {spec.name for spec in AGENT_REGISTRY}
    # The core agents must always be registered; later agents (tagger, refine,
    # and so on) may be added without breaking this contract.
    assert {"brand_research", "asset_pack", "avatar", "brand_critic"} <= names
    assert len(names) == len(AGENT_REGISTRY)  # names are unique


def test_manifest_is_json_serializable_and_complete() -> None:
    import json

    manifest = build_manifest()
    assert manifest["app"] == "brand-ops"
    assert len(manifest["agents"]) == len(AGENT_REGISTRY)
    for entry in manifest["agents"]:
        assert entry["name"]
        assert entry["description"]
        assert isinstance(entry["input_schema"], dict)
        assert isinstance(entry["scopes_required"], list) and entry["scopes_required"]
        assert entry["tier_required"]
    # Round-trips through JSON, which is what Brigade ingests.
    json.dumps(manifest)


def test_registry_agent_classes_are_agents() -> None:
    for spec in AGENT_REGISTRY:
        assert issubclass(spec.agent_cls, Agent)


def test_strong_model_agents_are_tagged_strong() -> None:
    by_name = {spec.name: spec for spec in AGENT_REGISTRY}
    assert by_name["brand_research"].model_tier == "strong"
    assert by_name["brand_critic"].model_tier == "strong"
    assert by_name["asset_pack"].model_tier == "default"
    assert by_name["avatar"].model_tier == "default"


def test_get_agent_spec_lookup_and_error() -> None:
    assert get_agent_spec("avatar").name == "avatar"
    with pytest.raises(KeyError):
        get_agent_spec("ghost")


# -- Brigade client degradation (no network) --------------------------------


def test_brigade_research_raises_when_unconfigured() -> None:
    client = BrigadeClient(Settings(brigade_base_url="https://h", brigade_api_key=""))
    with pytest.raises(BrigadeUnavailable):
        asyncio.run(client.research("anything"))


def test_brigade_research_raises_on_empty_topic() -> None:
    client = BrigadeClient(Settings(brigade_base_url="https://h", brigade_api_key="k"))
    with pytest.raises(BrigadeUnavailable):
        asyncio.run(client.research("  "))


def test_brigade_llm_chat_returns_none_when_unconfigured() -> None:
    client = BrigadeClient(Settings(llm_base_url="https://g", llm_api_key=""))
    assert asyncio.run(client.llm_chat([{"role": "user", "content": "hi"}])) is None


def test_brigade_llm_chat_returns_none_on_empty_messages() -> None:
    client = BrigadeClient(Settings(llm_base_url="https://g", llm_api_key="k"))
    assert asyncio.run(client.llm_chat([])) is None


def test_first_message_content_parsing() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
    assert _first_message_content(payload) == "hello"
    assert _first_message_content({"choices": []}) is None
    assert _first_message_content({}) is None
    assert _first_message_content("not a dict") is None


# -- BrandResearchAgent draft is pure ---------------------------------------


def test_draft_brand_yaml_is_pure_and_maps_schema() -> None:
    profile = {
        "display_name": "Acme Robotics",
        "tagline": "Industrial automation",
        "palette": {"primary": "#101010", "accent": "#ff8800", "ignored": 5},
        "fonts": {"primary_font": "Inter", "display_font": "Space Grotesk"},
        "voice": {"tone": ["plain", "direct"], "forbidden_phrases": ["synergy"]},
        "style_keywords": ["industrial", "clean", ""],
    }
    yaml_dict = BrandResearchAgent.draft_brand_yaml("Acme Robotics", profile)
    assert yaml_dict["brand"]["slug"] == "acme-robotics"
    assert yaml_dict["brand"]["display_name"] == "Acme Robotics"
    assert yaml_dict["visual"]["colors"] == {"primary": "#101010", "accent": "#ff8800"}
    assert yaml_dict["visual"]["typography"] == {
        "primary_font": "Inter",
        "display_font": "Space Grotesk",
    }
    # Empty style keyword is dropped.
    assert yaml_dict["visual"]["style_keywords"] == ["industrial", "clean"]
    assert yaml_dict["voice"]["tone"] == ["plain", "direct"]


def test_research_agent_run_research_only_when_no_llm() -> None:
    # Stub research succeeds, stub chat returns None: the agent reports
    # research_only and never raises.
    agent = BrandResearchAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    result = asyncio.run(agent.run({"company": "Acme", "domain": "acme.test"}))
    assert result["status"] == "research_only"
    assert result["company"] == "Acme"


def test_critic_run_skips_without_llm() -> None:
    agent = BrandCriticAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    result = asyncio.run(
        agent.run(
            {
                "asset": {"prompt": "a logo", "asset_type": "logo"},
                "brand_profile": {"palette": {"primary": "#000000"}},
            }
        )
    )
    assert result["status"] == "skipped"
