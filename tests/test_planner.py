"""Tests for the generation planner and its /api/plan-generation route.

No test here touches a real LLM or the network. The keyword heuristic is called
directly (it makes no LLM call), the agent is exercised through a stub Brigade
client that stands in for the gateway, and the route is hit with the in-process
TestClient while the LLM gateway env vars are cleared so the shared BrigadeClient
reports no gateway and the planner takes its deterministic fallback. The things
proven are:

  1. The keyword fallback turns "make an OG image for the investor update, dark"
     into a valid GenerateRequest (asset og_image, variant dark, topic about the
     investor update), with no live model.
  2. A model that picks an invalid brand or asset is rejected and coerced to a
     valid one, and the result still validates as a GenerateRequest.
  3. The endpoint answers 200 with the PlanGenerationReply shape, and never an
     invalid GenerateRequest.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brand_ops.agents.planner import GenerationPlanner, keyword_plan
from brand_ops.api.app import create_app
from brand_ops.api.schemas import GenerateRequest, PlanGenerationReply
from brand_ops.config import Settings


class _StubBrigade:
    """A stand-in Brigade client. Records the chat call; never hits the network.

    Mirrors the stub in tests/test_director.py so the planner is exercised through
    the same offline seam every other agent uses.
    """

    def __init__(self, chat_reply: str | None = None) -> None:
        self.chat_reply = chat_reply
        self.chat_calls: list[dict[str, Any]] = []
        self.last_usage: dict[str, Any] | None = None

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


_BRANDS = [
    {"slug": "magic-unicorn", "display_name": "Magic Unicorn"},
    {"slug": "center-deep", "display_name": "Center Deep"},
]


def _plan(agent: GenerationPlanner, text: str, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(agent.plan(text, _BRANDS, **kwargs))


# -- Heuristic: the canonical example, with no LLM --------------------------


def test_keyword_plan_handles_the_canonical_example() -> None:
    # The example the frontend stub was built around: an OG image, dark, about the
    # investor update. The heuristic must produce a valid GenerateRequest with the
    # right asset, variant, and topic, with no model in the loop.
    result = keyword_plan(
        "make an OG image for the investor update, dark", _BRANDS, brand="magic-unicorn"
    )
    request = result["request"]
    assert request["asset"] == "og_image"
    assert request["variant"] == "dark"
    assert "investor update" in (request["topic"] or "")
    assert request["brand"] == "magic-unicorn"
    # It is a fully valid GenerateRequest and a valid reply.
    GenerateRequest.model_validate(request)
    PlanGenerationReply.model_validate(result)
    # The rationale names the decisions, and the backend default is an assumption.
    assert any("OG share image" in line for line in result["rationale"])
    assert any("backend defaults to" in line for line in result["assumed"])


def test_keyword_plan_drops_a_variant_the_asset_does_not_offer() -> None:
    # "dark" is a generic keyword, but the avatar asset offers portrait/headshot/
    # mascot/abstract, not dark. The heuristic must drop it rather than return a
    # variant that would make the engine raise.
    result = keyword_plan("make an avatar, dark", _BRANDS, brand="center-deep")
    assert result["request"]["asset"] == "avatar"
    assert result["request"]["variant"] is None
    GenerateRequest.model_validate(result["request"])


def test_keyword_plan_falls_back_to_the_control_brand_and_asset() -> None:
    # A request that names no brand and no asset keeps the control values, so a
    # bare "make it dark" still yields a valid, complete request.
    result = keyword_plan(
        "make it dark", _BRANDS, brand="center-deep", asset="og_image"
    )
    assert result["request"]["brand"] == "center-deep"
    assert result["request"]["asset"] == "og_image"
    assert result["request"]["variant"] == "dark"
    GenerateRequest.model_validate(result["request"])


# -- Agent: the model path, validation, and fallbacks -----------------------


def test_planner_keeps_a_valid_model_pick() -> None:
    # The model returns a clean, valid pick. The planner keeps it and the result
    # validates as a GenerateRequest.
    model_json = json.dumps(
        {
            "brand": "magic-unicorn",
            "asset": "og_image",
            "variant": "dark",
            "topic": "investor update",
            "platform": None,
            "rationale": ["Asset: OG share image (matched the request)."],
        }
    )
    agent = GenerationPlanner(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _plan(
        agent, "make an OG image for the investor update, dark", brand="magic-unicorn"
    )
    request = result["request"]
    assert request["brand"] == "magic-unicorn"
    assert request["asset"] == "og_image"
    assert request["variant"] == "dark"
    assert request["topic"] == "investor update"
    GenerateRequest.model_validate(request)
    # The agent asked the gateway for JSON.
    assert agent.brigade.chat_calls
    assert agent.brigade.chat_calls[-1]["response_json"] is True


def test_planner_rejects_an_invalid_model_brand_and_asset() -> None:
    # The model invents a brand and an asset that do not exist. The planner must
    # reject both and coerce to a valid brand (the control) and a valid asset (the
    # control), never returning the bogus values.
    model_json = json.dumps(
        {
            "brand": "totally-made-up",
            "asset": "hologram",
            "variant": "dark",
            "topic": "launch",
            "rationale": ["picked a hologram"],
        }
    )
    agent = GenerationPlanner(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _plan(agent, "do a thing", brand="center-deep", asset="og_image")
    request = result["request"]
    assert request["brand"] == "center-deep"
    assert request["asset"] == "og_image"
    # The whole thing is still a valid GenerateRequest.
    GenerateRequest.model_validate(request)


def test_planner_coerces_an_invalid_brand_to_the_first_when_no_control() -> None:
    # The model's brand is invalid and there is no brand control either. The
    # planner falls back to the first brand rather than emitting the bad slug.
    model_json = json.dumps({"brand": "nope", "asset": "logo"})
    agent = GenerationPlanner(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _plan(agent, "make a logo")
    assert result["request"]["brand"] == _BRANDS[0]["slug"]
    assert result["request"]["asset"] == "logo"
    GenerateRequest.model_validate(result["request"])


def test_planner_falls_back_to_heuristic_without_a_gateway() -> None:
    # The stub returns None (no gateway). The planner must fall back to the keyword
    # heuristic and still return a valid plan for the canonical example.
    agent = GenerationPlanner(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    result = _plan(
        agent, "make an OG image for the investor update, dark", brand="magic-unicorn"
    )
    assert result["request"]["asset"] == "og_image"
    assert result["request"]["variant"] == "dark"
    GenerateRequest.model_validate(result["request"])


def test_planner_falls_back_to_heuristic_on_unparseable_reply() -> None:
    # The model ignored the JSON instruction and answered in prose. The planner
    # must fall back to the heuristic rather than fail.
    agent = GenerationPlanner(
        brigade=_StubBrigade(chat_reply="I would make a logo for you."), settings=Settings()
    )
    result = _plan(agent, "make a logo", brand="magic-unicorn")
    assert result["request"]["asset"] == "logo"
    GenerateRequest.model_validate(result["request"])


# -- Route: 200 with the PlanGenerationReply shape --------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off and no LLM gateway, made explicit regardless of ambient env so the
    # route takes the deterministic no-gateway path and never reaches a live model.
    # Same posture as tests/test_director.py.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(create_app())


def test_route_returns_plan_without_gateway(client: TestClient) -> None:
    # No gateway: the route falls back to the heuristic and answers 200 with a
    # valid PlanGenerationReply for the canonical example.
    resp = client.post(
        "/api/plan-generation",
        json={
            "text": "make an OG image for the investor update, dark",
            "brand": "magic-unicorn",
        },
    )
    assert resp.status_code == 200, resp.text
    reply = PlanGenerationReply.model_validate(resp.json())
    assert reply.request.asset == "og_image"
    assert reply.request.variant == "dark"
    assert "investor update" in (reply.request.topic or "")
    # The brand is a real seeded slug.
    assert reply.request.brand == "magic-unicorn"
    assert reply.rationale


def test_route_with_stubbed_gateway_returns_validated_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the agent's chat to return a JSON pick (no network), proving the route
    # parses and validates a model reply into a PlanGenerationReply and answers
    # 200. The model picks an invalid asset; the route's validation coerces it.
    pick = json.dumps(
        {
            "brand": "magic-unicorn",
            "asset": "not-real",
            "variant": "dark",
            "topic": "investor update",
            "rationale": ["bad asset"],
        }
    )

    async def _fake_chat(self: GenerationPlanner, *args: Any, **kwargs: Any) -> str:
        return pick

    monkeypatch.setattr(GenerationPlanner, "chat", _fake_chat)

    resp = client.post(
        "/api/plan-generation",
        json={
            "text": "make an OG image for the investor update, dark",
            "brand": "magic-unicorn",
            "asset": "og_image",
        },
    )
    assert resp.status_code == 200, resp.text
    reply = PlanGenerationReply.model_validate(resp.json())
    # The invalid model asset was coerced to the valid control asset.
    assert reply.request.asset == "og_image"
    assert reply.request.brand == "magic-unicorn"


def test_route_defaults_brand_to_first_when_text_names_none(client: TestClient) -> None:
    # No brand control and a text that names no brand: the route still returns a
    # valid request whose brand is a real seeded slug (the first), never null.
    resp = client.post(
        "/api/plan-generation",
        json={"text": "make a logo"},
    )
    assert resp.status_code == 200, resp.text
    reply = PlanGenerationReply.model_validate(resp.json())
    assert reply.request.brand
    assert reply.request.asset == "logo"
