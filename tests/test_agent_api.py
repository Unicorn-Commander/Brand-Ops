"""REST API tests for the agent endpoints.

These exercise GET /api/agents and POST /api/agents/{name}/run with the
in-process TestClient and no network. Auth is off (AUTH_REQUIRED unset) so every
request resolves to the anonymous principal. No live LLM is called: the LLM
gateway env vars are cleared so the shared BrigadeClient reports no gateway, and
the only agent run exercised here (refine) is fully deterministic on that path,
so its result is fixed and reproducible offline.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops.agents.registry import AGENT_REGISTRY
from brand_ops.api.app import create_app

# The six agent names the registry exposes. Sourced from the registry so this
# test tracks it rather than hard-coding a list that could drift.
_EXPECTED_AGENTS = {spec.name for spec in AGENT_REGISTRY}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off, and no LLM gateway: make both explicit and deterministic
    # regardless of the ambient env, so the refine run takes its pure path and
    # never reaches a live model.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(create_app())


def test_list_agents_returns_the_six_agents(client: TestClient) -> None:
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert "agents" in body
    agents = body["agents"]
    assert isinstance(agents, list)
    assert len(agents) == 6

    names = {a["name"] for a in agents}
    assert names == _EXPECTED_AGENTS

    # Each entry carries the fields the GUI needs to render and gate the agent.
    for agent in agents:
        assert agent["name"]
        assert agent["description"]
        assert isinstance(agent["input_schema"], dict)
        assert isinstance(agent["scopes_required"], list)
        assert agent["tier_required"]


def test_run_refine_applies_tweaks_with_no_llm(client: TestClient) -> None:
    # refine merges the critic's prompt_tweaks into the base prompt
    # deterministically; with no LLM gateway the smoothing layer is skipped and
    # the deterministic merge is returned, so this needs no model.
    resp = client.post(
        "/api/agents/refine/run",
        json={
            "asset": {"prompt": "a teal logo"},
            "critique": {"prompt_tweaks": ["add a deep navy background"]},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"] == "refine"
    assert body["status"] == "ok"

    result = body["result"]
    refined = result["refined_prompt"]
    assert isinstance(refined, str) and refined
    # The base prompt is preserved and the tweak is folded in.
    assert "teal logo" in refined
    assert "deep navy background" in refined
    # No gateway means the deterministic merge stands; nothing was smoothed.
    assert result["smoothed"] is False
    assert result["applied_tweaks"] == ["add a deep navy background"]


def test_run_unknown_agent_is_404(client: TestClient) -> None:
    resp = client.post("/api/agents/nope/run", json={})
    assert resp.status_code == 404
