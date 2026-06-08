"""Unit tests for the TaggerAgent and RefineAgent, plus their registry entries.

No test here touches a real LLM or the network. The pure logic is exercised
directly: the tagger's asset_type coercion and tag cleaning, and the refine
agent's deterministic tweak extraction and merge. Where an agent is exercised
through run, a stub Brigade client stands in for the gateway, and the skip path
(no gateway configured) is checked without any stub at all.
"""
from __future__ import annotations

import asyncio
from typing import Any

# Import both new agents and the registry. This is the import-check half: if
# either module is broken, collection fails here.
from brand_ops.agents import AGENT_REGISTRY, Agent
from brand_ops.agents.refine import RefineAgent
from brand_ops.agents.registry import build_manifest, get_agent_spec
from brand_ops.agents.tagger import TaggerAgent
from brand_ops.asset_types import ASSET_TYPES
from brand_ops.config import Settings


class _StubBrigade:
    """A stand-in Brigade client. Records calls; never touches the network.

    The llm_chat signature mirrors the real client (model positional, the rest
    keyword-only) so agents bind to it exactly as they would in production.
    """

    def __init__(self, chat_reply: str | None = None) -> None:
        self.chat_reply = chat_reply
        self.chat_calls: list[dict[str, Any]] = []

    async def llm_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        *,
        temperature: float = 0.4,
        response_json: bool = False,
    ) -> str | None:
        self.chat_calls.append(
            {"messages": messages, "model": model, "response_json": response_json}
        )
        return self.chat_reply


def _no_llm_settings() -> Settings:
    """Settings with no LLM gateway, so a real BrigadeClient returns None."""
    return Settings(llm_base_url="https://gateway.invalid", llm_api_key="")


# -- TaggerAgent.coerce_asset_type (pure) -----------------------------------


def test_coerce_passes_through_every_canonical_type() -> None:
    # Every one of the six known types coerces to itself unchanged.
    for key in ASSET_TYPES:
        assert TaggerAgent.coerce_asset_type(key) == key
    assert set(TaggerAgent.known_types()) == set(ASSET_TYPES)


def test_coerce_is_case_and_separator_insensitive() -> None:
    assert TaggerAgent.coerce_asset_type("App Icon") == "app_icon"
    assert TaggerAgent.coerce_asset_type("app-icon") == "app_icon"
    assert TaggerAgent.coerce_asset_type("  APP_ICON ") == "app_icon"
    assert TaggerAgent.coerce_asset_type("OG Image") == "og_image"


def test_coerce_maps_known_aliases() -> None:
    cases = {
        "icon": "app_icon",
        "favicon": "app_icon",
        "logomark": "logo",
        "wordmark": "logo",
        "og": "og_image",
        "opengraph": "og_image",
        "podcast": "podcast_cover",
        "cover art": "podcast_cover",
        "instagram": "social_card",
        "banner": "social_card",
        "headshot": "avatar",
        "profile pic": "avatar",
    }
    for label, expected in cases.items():
        assert TaggerAgent.coerce_asset_type(label) == expected, label


def test_coerce_unknown_falls_back_to_default() -> None:
    # Junk and non-strings land on the documented fallback, never an unknown.
    assert TaggerAgent.coerce_asset_type("totally-unknown-thing") == "social_card"
    assert TaggerAgent.coerce_asset_type("") == "social_card"
    assert TaggerAgent.coerce_asset_type(None) == "social_card"
    assert TaggerAgent.coerce_asset_type(42) == "social_card"


def test_coerce_honors_a_valid_default_for_unknown_input() -> None:
    # When the caller passes the current type as the default, an unrecognized
    # suggestion falls back to that current type.
    assert TaggerAgent.coerce_asset_type("nonsense", default="logo") == "logo"
    # A bad default itself is ignored in favor of the module fallback.
    assert TaggerAgent.coerce_asset_type("nonsense", default="not-a-type") == "social_card"


def test_coerce_result_is_always_a_known_type() -> None:
    for value in ["logo", "icon", "weird", "", None, 7, "PODCAST", "social card"]:
        assert TaggerAgent.coerce_asset_type(value) in ASSET_TYPES


# -- TaggerAgent message build + run paths (stubbed) ------------------------


def test_build_messages_text_only_without_image_url() -> None:
    messages = TaggerAgent._build_messages({"asset_type": "logo", "brand": "acme"})
    assert messages[0]["role"] == "system"
    user = messages[1]
    assert user["role"] == "user"
    assert isinstance(user["content"], str)  # plain text path


def test_build_messages_forwards_image_url_as_parts() -> None:
    messages = TaggerAgent._build_messages(
        {"asset_type": "logo", "image_url": "https://img.test/a.png"}
    )
    content = messages[1]["content"]
    assert isinstance(content, list)
    kinds = {part["type"] for part in content}
    assert kinds == {"text", "image_url"}
    image_part = next(p for p in content if p["type"] == "image_url")
    assert image_part["image_url"]["url"] == "https://img.test/a.png"


def test_tagger_run_requires_asset() -> None:
    agent = TaggerAgent(brigade=_StubBrigade(), settings=Settings())
    result = asyncio.run(agent.run({}))
    assert result["status"] == "error"


def test_tagger_run_skips_without_llm_but_echoes_coerced_type() -> None:
    # No gateway configured: the real client returns None, so the agent skips
    # the classification but still returns a coerced current type to apply.
    agent = TaggerAgent(settings=_no_llm_settings())
    result = asyncio.run(agent.run({"asset": {"asset_type": "icon", "brand": "acme"}}))
    assert result["status"] == "skipped"
    assert result["asset_type"] == "app_icon"
    assert result["tags"] == []
    assert result["belongs_to_brand"] is True


def test_tagger_run_normalizes_a_stubbed_model_answer() -> None:
    reply = (
        '{"asset_type": "icon", "tags": ["Blue", "blue", "minimal", ""], '
        '"belongs_to_brand": "no", "confidence": 5, "notes": "  off-brand  "}'
    )
    agent = TaggerAgent(brigade=_StubBrigade(chat_reply=reply), settings=Settings())
    result = asyncio.run(agent.run({"asset": {"asset_type": "logo", "brand": "acme"}}))
    assert result["status"] == "ok"
    # asset_type coerced from the alias "icon".
    assert result["asset_type"] == "app_icon"
    # tags lowercased, de-duplicated, blanks dropped, order preserved.
    assert result["tags"] == ["blue", "minimal"]
    # "no" string parsed to False.
    assert result["belongs_to_brand"] is False
    # confidence clamped into 0..1.
    assert result["confidence"] == 1.0
    assert result["notes"] == "off-brand"


def test_tagger_run_unparsed_when_model_returns_garbage() -> None:
    agent = TaggerAgent(brigade=_StubBrigade(chat_reply="not json at all"), settings=Settings())
    result = asyncio.run(agent.run({"asset": {"asset_type": "logo"}}))
    assert result["status"] == "unparsed"


def test_tagger_run_falls_back_to_current_type_when_model_omits_it() -> None:
    # Model answers with no asset_type; the agent keeps the (coerced) current.
    reply = '{"tags": ["clean"], "belongs_to_brand": true, "confidence": 0.4}'
    agent = TaggerAgent(brigade=_StubBrigade(chat_reply=reply), settings=Settings())
    result = asyncio.run(agent.run({"asset": {"asset_type": "favicon"}}))
    assert result["status"] == "ok"
    assert result["asset_type"] == "app_icon"  # favicon -> app_icon


# -- RefineAgent.extract_tweaks (pure) --------------------------------------


def test_extract_tweaks_from_full_critic_result() -> None:
    critic_result = {
        "status": "ok",
        "critique": {
            "on_brand": False,
            "prompt_tweaks": ["use the brand navy", "add more whitespace"],
        },
    }
    assert RefineAgent.extract_tweaks(critic_result) == [
        "use the brand navy",
        "add more whitespace",
    ]


def test_extract_tweaks_from_bare_critique_dict() -> None:
    assert RefineAgent.extract_tweaks({"prompt_tweaks": ["a", "b"]}) == ["a", "b"]


def test_extract_tweaks_from_plain_list() -> None:
    assert RefineAgent.extract_tweaks(["x", "y"]) == ["x", "y"]


def test_extract_tweaks_cleans_blanks_and_duplicates() -> None:
    tweaks = RefineAgent.extract_tweaks(
        {"prompt_tweaks": ["  keep navy ", "keep navy", "", "  ", "add grid"]}
    )
    assert tweaks == ["keep navy", "add grid"]


def test_extract_tweaks_tolerates_missing_or_wrong_shapes() -> None:
    assert RefineAgent.extract_tweaks(None) == []
    assert RefineAgent.extract_tweaks({}) == []
    assert RefineAgent.extract_tweaks({"critique": {"prompt_tweaks": "not a list"}}) == []
    assert RefineAgent.extract_tweaks(42) == []


# -- RefineAgent.merge_tweaks (pure) ----------------------------------------


def test_merge_tweaks_appends_directives_in_order() -> None:
    merged, applied = RefineAgent.merge_tweaks(
        "A flat logo for Acme",
        ["use navy #101840", "add generous whitespace"],
    )
    assert applied == ["use navy #101840", "add generous whitespace"]
    assert merged == "A flat logo for Acme. use navy #101840. add generous whitespace."


def test_merge_tweaks_skips_tweaks_already_in_prompt() -> None:
    merged, applied = RefineAgent.merge_tweaks(
        "A flat logo using navy on white",
        ["using navy", "add a subtle grid"],
    )
    # "using navy" already present (case-insensitive) and is not restated.
    assert applied == ["add a subtle grid"]
    assert merged == "A flat logo using navy on white. add a subtle grid."


def test_merge_tweaks_no_applicable_tweaks_returns_prompt_unchanged() -> None:
    merged, applied = RefineAgent.merge_tweaks("A clean wordmark", [])
    assert merged == "A clean wordmark"
    assert applied == []
    # A tweak that is fully contained in the prompt also yields no change.
    merged2, applied2 = RefineAgent.merge_tweaks("A clean wordmark", ["clean wordmark"])
    assert merged2 == "A clean wordmark"
    assert applied2 == []


def test_merge_tweaks_does_not_double_periods() -> None:
    # Base prompt ends with a period and the tweak does too; the join strips
    # both so the result has no doubled period.
    merged, applied = RefineAgent.merge_tweaks("Base prompt.", ["raise contrast."])
    assert applied == ["raise contrast."]
    assert ".." not in merged
    assert merged == "Base prompt. raise contrast."


def test_merge_tweaks_is_deterministic() -> None:
    args = ("A logo", ["one", "two", "three"])
    assert RefineAgent.merge_tweaks(*args) == RefineAgent.merge_tweaks(*args)


# -- RefineAgent run paths (stubbed) ----------------------------------------


def test_refine_run_requires_a_base_prompt() -> None:
    agent = RefineAgent(brigade=_StubBrigade(), settings=Settings())
    result = asyncio.run(agent.run({"asset": {}, "critique": {"prompt_tweaks": ["x"]}}))
    assert result["status"] == "error"


def test_refine_run_no_tweaks_returns_prompt_unchanged_without_calling_llm() -> None:
    stub = _StubBrigade(chat_reply="SHOULD NOT BE USED")
    agent = RefineAgent(brigade=stub, settings=Settings())
    result = asyncio.run(
        agent.run({"asset": {"prompt": "A flat logo"}, "critique": {"prompt_tweaks": []}})
    )
    assert result["status"] == "ok"
    assert result["refined_prompt"] == "A flat logo"
    assert result["applied_tweaks"] == []
    # With no applicable tweaks the agent must not call the gateway.
    assert stub.chat_calls == []


def test_refine_run_deterministic_when_smoothing_disabled() -> None:
    stub = _StubBrigade(chat_reply="SHOULD NOT BE USED")
    agent = RefineAgent(brigade=stub, settings=Settings())
    result = asyncio.run(
        agent.run(
            {
                "asset": {"prompt": "A flat logo for Acme"},
                "critique": {"critique": {"prompt_tweaks": ["use navy", "add whitespace"]}},
                "smooth": False,
            }
        )
    )
    assert result["status"] == "ok"
    assert result["applied_tweaks"] == ["use navy", "add whitespace"]
    assert result["refined_prompt"] == "A flat logo for Acme. use navy. add whitespace."
    assert result["smoothed"] is False
    assert stub.chat_calls == []  # smoothing off: no gateway call


def test_refine_run_uses_smoothed_text_when_gateway_returns_one() -> None:
    stub = _StubBrigade(chat_reply="A flat Acme logo in navy with generous whitespace")
    agent = RefineAgent(brigade=stub, settings=Settings())
    result = asyncio.run(
        agent.run(
            {
                "asset": {"prompt": "A flat logo for Acme"},
                "critique": {"prompt_tweaks": ["use navy", "add whitespace"]},
            }
        )
    )
    assert result["status"] == "ok"
    assert result["refined_prompt"] == "A flat Acme logo in navy with generous whitespace"
    assert result["smoothed"] is True
    assert result["applied_tweaks"] == ["use navy", "add whitespace"]
    assert len(stub.chat_calls) == 1


def test_refine_run_falls_back_to_merge_when_no_llm() -> None:
    # No gateway configured: smoothing is requested but unavailable, so the
    # deterministic merge is returned and smoothed is False.
    agent = RefineAgent(settings=_no_llm_settings())
    result = asyncio.run(
        agent.run(
            {
                "prompt": "A flat logo for Acme",
                "critique": {"prompt_tweaks": ["use navy"]},
            }
        )
    )
    assert result["status"] == "ok"
    assert result["smoothed"] is False
    assert result["refined_prompt"] == "A flat logo for Acme. use navy."


# -- Registry entries for the two new agents --------------------------------


def test_registry_includes_tagger_and_refine() -> None:
    names = {spec.name for spec in AGENT_REGISTRY}
    assert {"tagger", "refine"} <= names


def test_new_agents_specs_are_well_formed() -> None:
    for name in ("tagger", "refine"):
        spec = get_agent_spec(name)
        assert issubclass(spec.agent_cls, Agent)
        assert spec.description
        assert isinstance(spec.input_schema, dict)
        assert spec.input_schema["type"] == "object"
        assert spec.scopes_required  # non-empty
        assert spec.tier_required
        # Both run on the default (Qwen-first) model tier.
        assert spec.model_tier == "default"


def test_new_agents_appear_in_manifest_and_serialize() -> None:
    import json

    manifest = build_manifest()
    entries = {entry["name"]: entry for entry in manifest["agents"]}
    assert "tagger" in entries
    assert "refine" in entries
    # Required input keys are declared as the spec promises.
    assert entries["tagger"]["input_schema"]["required"] == ["asset"]
    assert entries["refine"]["input_schema"]["required"] == ["asset", "critique"]
    # The whole manifest still round-trips through JSON.
    json.dumps(manifest)
