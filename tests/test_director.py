"""Tests for the Director agent (Iris) and its /api/director/chat route.

No test here touches a real LLM or the network. The agent is exercised through
run with a stub Brigade client that stands in for the gateway, and the route is
hit with the in-process TestClient while the LLM gateway env vars are cleared so
the shared BrigadeClient reports no gateway. The two things proven are:

  1. A well-formed JSON reply from the model parses into the DirectorReply shape
     and its intent/brand/scope fields are normalized.
  2. A None gateway (no LLM) yields the graceful in-persona fallback with intent
     "chat", and the route answers 200 on both the parsed and the fallback path.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from brand_ops import agent_identity
from brand_ops.agents.director import DirectorAgent
from brand_ops.api.app import create_app
from brand_ops.api.schemas import DirectorReply
from brand_ops.config import Settings

_REPO = Path(__file__).resolve().parents[1]


class _StubBrigade:
    """A stand-in Brigade client. Records the chat call; never hits the network.

    Mirrors the stub in tests/test_agents.py so the Director is exercised through
    the same offline seam every other agent uses.
    """

    def __init__(self, chat_reply: str | None = None) -> None:
        self.chat_reply = chat_reply
        self.chat_calls: list[dict[str, Any]] = []

    async def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
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


_BRANDS_CONTEXT = [
    {"slug": "magic-unicorn", "display_name": "Magic Unicorn"},
    {"slug": "center-deep", "display_name": "Center Deep"},
]


def _run(agent: DirectorAgent, payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(agent.run(payload))


# -- Agent: a JSON reply parses into DirectorReply --------------------------


def test_director_parses_model_json_into_director_reply() -> None:
    # The model returns a clean JSON object asking for a pack. The agent should
    # normalize it and the result should validate as a DirectorReply.
    model_json = json.dumps(
        {
            "reply": "I have Magic Unicorn loaded. I will build the core pack now.",
            "intent": "pack",
            "brand": "magic-unicorn",
            "asset": None,
            "variant": None,
            "pack": "core",
            "topic": None,
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(
        agent,
        {
            "message": "make me the core pack",
            "brand": "magic-unicorn",
            "history": [],
            "brands_context": _BRANDS_CONTEXT,
        },
    )

    # Validates against the wire contract.
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "pack"
    assert reply.pack == "core"
    assert reply.brand == "magic-unicorn"
    assert reply.reply.startswith("I have Magic Unicorn loaded")
    # Pack intent carries no single-asset scope.
    assert reply.asset is None
    assert reply.variant is None

    # The agent asked the gateway for JSON, the response_json contract.
    assert agent.brigade.chat_calls
    assert agent.brigade.chat_calls[-1]["response_json"] is True


def test_director_single_intent_keeps_asset_scope_and_echoes_active_brand() -> None:
    # The model omits brand on a single-asset ask; the agent echoes the active
    # brand and keeps the asset/variant scope for the single intent.
    model_json = json.dumps(
        {
            "reply": "On it. One logo in color.",
            "intent": "single",
            "asset": "logo",
            "variant": "color",
            "topic": "spring launch",
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(
        agent,
        {
            "message": "I need a logo",
            "brand": "center-deep",
            "brands_context": _BRANDS_CONTEXT,
        },
    )
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "single"
    assert reply.asset == "logo"
    assert reply.variant == "color"
    assert reply.topic == "spring launch"
    # Brand was absent in the model JSON, so the active brand is echoed.
    assert reply.brand == "center-deep"
    # A single intent carries no pack.
    assert reply.pack is None


def test_director_unknown_intent_is_coerced_to_chat() -> None:
    # An intent outside the known set must not reach the frontend; it becomes
    # chat, and chat strips every scope field.
    model_json = json.dumps(
        {
            "reply": "Let me think about that.",
            "intent": "teleport",
            "asset": "logo",
            "pack": "core",
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(agent, {"message": "do something", "brand": None, "brands_context": []})
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "chat"
    assert reply.asset is None
    assert reply.pack is None


# -- Agent: the create-an-agent intent carries name + role ------------------


def test_director_agent_intent_carries_name_and_role() -> None:
    # The model classifies a create-an-agent ask. The normalized result keeps
    # intent "agent" and surfaces the helper agent_name/agent_role the route
    # consumes (these are not on the wire schema, so read them off the raw dict).
    model_json = json.dumps(
        {
            "reply": "I will add Atlas as your social media manager.",
            "intent": "agent",
            "brand": "magic-unicorn",
            "agent_name": "Atlas",
            "agent_role": "social media manager",
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(
        agent,
        {
            "message": "create an agent named Atlas who is the social media manager",
            "brand": "magic-unicorn",
            "brands_context": _BRANDS_CONTEXT,
        },
    )
    assert result["intent"] == "agent"
    assert result["brand"] == "magic-unicorn"
    assert result["agent_name"] == "Atlas"
    assert result["agent_role"] == "social media manager"
    # It still validates as a DirectorReply (the helper keys are ignored).
    assert DirectorReply.model_validate(result).intent == "agent"


def test_director_agent_intent_defaults_role_when_unstated() -> None:
    # The operator named an agent but did not say what it does. The role defaults
    # to "brand steward" so the route can still create a usable identity.
    model_json = json.dumps(
        {
            "reply": "I will spin up Scout for the team.",
            "intent": "agent",
            "brand": "magic-unicorn",
            "agent_name": "Scout",
            "agent_role": None,
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(agent, {"message": "spin up an agent named Scout", "brand": "magic-unicorn"})
    assert result["intent"] == "agent"
    assert result["agent_name"] == "Scout"
    assert result["agent_role"] == "brand steward"


def test_director_agent_intent_without_name_degrades_to_chat() -> None:
    # An agent ask with no extractable name is not actionable as a creation; the
    # agent downgrades it to chat (keeping Iris's words) so the route never tries
    # a nameless create.
    model_json = json.dumps(
        {
            "reply": "Which brand should the new agent belong to, and what is its name?",
            "intent": "agent",
            "brand": "magic-unicorn",
            "agent_name": None,
            "agent_role": "brand steward",
        }
    )
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=model_json), settings=Settings())
    result = _run(agent, {"message": "make me a new agent", "brand": "magic-unicorn"})
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "chat"
    # The role helper is cleared once the intent is no longer "agent".
    assert result["agent_name"] is None
    assert result["agent_role"] is None


# -- Agent: no gateway yields the graceful fallback -------------------------


def test_director_none_gateway_yields_graceful_chat_fallback() -> None:
    # The stub returns None (no LLM gateway). The agent must answer in persona
    # with intent chat and no scope fields, never raise.
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    result = _run(
        agent,
        {
            "message": "hello",
            "brand": "magic-unicorn",
            "brands_context": _BRANDS_CONTEXT,
        },
    )
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "chat"
    assert reply.asset is None and reply.pack is None and reply.variant is None
    # In-persona, first person, and scoped to the active brand.
    assert reply.reply
    assert reply.reply.startswith("I ")
    assert "magic-unicorn" in reply.reply


def test_director_empty_message_no_brand_opens_in_persona_offline() -> None:
    # Empty Studio, no brand, no gateway: Iris opens with her introduction
    # rather than returning a blank reply.
    agent = DirectorAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    result = _run(agent, {"message": "", "brand": None, "brands_context": []})
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "chat"
    assert "I am Iris" in reply.reply


def test_director_unparseable_reply_salvages_prose_as_chat() -> None:
    # The model ignored the JSON instruction and answered in prose. The agent
    # surfaces that prose as Iris's reply and falls back to intent chat.
    agent = DirectorAgent(
        brigade=_StubBrigade(chat_reply="I would start with the logo, then the icon."),
        settings=Settings(),
    )
    result = _run(agent, {"message": "where do we start", "brand": "magic-unicorn"})
    reply = DirectorReply.model_validate(result)
    assert reply.intent == "chat"
    assert reply.reply == "I would start with the logo, then the icon."


# -- Route: 200 on both the parsed and the fallback path --------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off and no LLM gateway, made explicit regardless of ambient env so
    # the route takes the deterministic no-gateway path and never reaches a live
    # model. This is the same posture as tests/test_agent_api.py.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(create_app())


def test_route_returns_200_graceful_fallback_without_gateway(client: TestClient) -> None:
    # No gateway configured: the route must answer 200 with an in-persona chat
    # reply, never 500.
    resp = client.post(
        "/api/director/chat",
        json={"message": "what can you do", "brand": None, "history": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    reply = DirectorReply.model_validate(body)
    assert reply.intent == "chat"
    assert reply.reply


def test_route_accepts_history_and_active_brand(client: TestClient) -> None:
    # A request carrying history and an active brand is accepted and answered
    # 200 on the no-gateway path. The active brand is echoed back.
    resp = client.post(
        "/api/director/chat",
        json={
            "message": "remind me where we left off",
            "brand": "acme",
            "history": [
                {"role": "agent", "text": "I am Iris, your brand director."},
                {"role": "user", "text": "let us work on the logo"},
            ],
        },
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "chat"
    assert reply.brand == "acme"


def test_route_with_stubbed_gateway_returns_routed_intent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the agent's chat to return a JSON pack reply (no network), proving
    # the route parses a model reply into a routed DirectorReply and answers
    # 200. This exercises the parsed path end to end through the endpoint.
    pack_json = json.dumps(
        {
            "reply": "Building the social pack for that brand now.",
            "intent": "pack",
            "brand": "acme",
            "pack": "social",
        }
    )

    async def _fake_chat(self: DirectorAgent, *args: Any, **kwargs: Any) -> str:
        return pack_json

    monkeypatch.setattr(DirectorAgent, "chat", _fake_chat)

    resp = client.post(
        "/api/director/chat",
        json={"message": "make social assets", "brand": "acme"},
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "pack"
    assert reply.pack == "social"
    assert reply.brand == "acme"


# -- Route: the agent intent creates (or surfaces) an on-brand identity ------
#
# These exercise the agentic create-an-agent path end to end through the
# endpoint. The whole app (loader, agent_identity, routes) is pointed at a tmp
# kits dir seeded with a real brand.yaml so create_agent writes there and the
# brand resolves, and DirectorAgent.run is patched to return a normalized
# agent-intent result (no LLM, no network), mirroring how the pack test patches
# DirectorAgent.chat.


@pytest.fixture
def agent_kits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A tmp kits dir with a real brand, wired into every module that reads it.

    DEFAULT_KITS_DIR is imported by value in three places (the loader, the
    agent-identity module, and the routes), so each is patched to the tmp tree,
    exactly as tests/test_agent_identity.py does for its HTTP tests.
    """
    from brand_ops import brand_loader

    src = _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml"
    brand_dir = tmp_path / "magic-unicorn"
    brand_dir.mkdir()
    shutil.copy(src, brand_dir / "brand.yaml")

    # One source point: every module resolves its root through
    # brand_loader.current_kits_dir(), which reads this (unscoped -> the flat root).
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def agent_client(monkeypatch: pytest.MonkeyPatch, agent_kits: Path) -> TestClient:
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    return TestClient(create_app())


def _patch_run(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> None:
    """Make DirectorAgent.run return ``result`` verbatim (no LLM, no network)."""

    async def _fake_run(self: DirectorAgent, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(result)

    monkeypatch.setattr(DirectorAgent, "run", _fake_run)


def test_route_agent_intent_creates_identity_and_echoes_agent(
    agent_client: TestClient, agent_kits: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Iris classified a create-an-agent ask. The route must create the on-brand
    # identity, echo agent={slug,name,role,brand}, narrate it in first person,
    # and offer the avatar.
    _patch_run(
        monkeypatch,
        {
            "reply": "I will add Atlas.",
            "intent": "agent",
            "brand": "magic-unicorn",
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
            "agent_name": "Atlas",
            "agent_role": "social media manager",
        },
    )

    resp = agent_client.post(
        "/api/director/chat",
        json={
            "message": "create an agent named Atlas who is the social media manager",
            "brand": "magic-unicorn",
        },
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "agent"
    assert reply.brand == "magic-unicorn"
    assert reply.agent == {
        "slug": "atlas",
        "name": "Atlas",
        "role": "social media manager",
        "brand": "magic-unicorn",
    }
    # Iris speaks first person and offers the avatar.
    assert reply.reply.startswith("Done.")
    assert "avatar" in reply.reply.lower()
    # The identity was actually written to disk under the brand.
    assert (agent_kits / "magic-unicorn" / "agents" / "atlas" / "agent.json").is_file()
    # And it shows up on the brand's agents endpoint.
    listed = agent_client.get("/api/brands/magic-unicorn/agents")
    assert listed.status_code == 200
    assert any(a["slug"] == "atlas" for a in listed.json()["agents"])


def test_route_agent_intent_duplicate_returns_existing(
    agent_client: TestClient, agent_kits: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The agent already exists. The route must surface the existing one (not
    # 500, not 409), narrate that it already has them, and still offer the
    # avatar.
    agent_identity.create_agent(
        "magic-unicorn", "Atlas", "support lead", kits_dir=agent_kits
    )
    _patch_run(
        monkeypatch,
        {
            "reply": "Adding Atlas.",
            "intent": "agent",
            "brand": "magic-unicorn",
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
            "agent_name": "Atlas",
            # A different role on the duplicate ask: the existing identity's role
            # wins, since the route does not overwrite an existing agent.
            "agent_role": "social media manager",
        },
    )

    resp = agent_client.post(
        "/api/director/chat",
        json={"message": "add an agent named Atlas", "brand": "magic-unicorn"},
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "agent"
    assert reply.agent["slug"] == "atlas"
    # The stored role is preserved, not overwritten by the new ask.
    assert reply.agent["role"] == "support lead"
    assert "already" in reply.reply.lower()


def test_route_agent_intent_creation_error_degrades_to_chat(
    agent_client: TestClient, agent_kits: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # create_agent blows up. The route must never 500: it degrades to a normal
    # in-persona chat reply with no agent payload.
    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(agent_identity, "create_agent", _boom)
    _patch_run(
        monkeypatch,
        {
            "reply": "I will add Atlas.",
            "intent": "agent",
            "brand": "magic-unicorn",
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
            "agent_name": "Atlas",
            "agent_role": "social media manager",
        },
    )

    resp = agent_client.post(
        "/api/director/chat",
        json={"message": "create an agent named Atlas", "brand": "magic-unicorn"},
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "chat"
    assert reply.agent is None
    assert reply.reply


def test_route_agent_intent_unknown_brand_degrades_to_chat(
    agent_client: TestClient, agent_kits: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The resolved brand does not exist (BrandNotFound from create_agent). The
    # route degrades to chat rather than 500 or 404.
    _patch_run(
        monkeypatch,
        {
            "reply": "Adding Atlas.",
            "intent": "agent",
            "brand": "no-such-brand",
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
            "agent_name": "Atlas",
            "agent_role": "social media manager",
        },
    )

    resp = agent_client.post(
        "/api/director/chat",
        json={"message": "create an agent named Atlas", "brand": "no-such-brand"},
    )
    assert resp.status_code == 200
    reply = DirectorReply.model_validate(resp.json())
    assert reply.intent == "chat"
    assert reply.agent is None
