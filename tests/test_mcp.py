"""Tests for the Brand-Ops MCP server.

These exercise the read tools only. They never call live generation, so no
backend, network, or spend is involved. FastMCP's @mcp.tool() returns the
underlying function unchanged, so each tool is callable directly here.

The agent-identity tools that would generate an image stub the backend so no
network or spend is involved, and run against a tmp kits dir seeded with a real
brand.yaml (mirroring tests/test_agent_identity.py).
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

# Import the api package first so its module-load cycle (api.app -> mcp_http ->
# mcp_server) initializes with the api side as the entry point. mcp_server imports
# api.catalog at module load, so importing mcp_server FIRST in isolation hits a
# known partial-init circular import; importing api first resolves it. This makes
# the module collectible on its own (e.g. ``pytest tests/test_mcp.py``) rather
# than only when an earlier-collected test happens to import api first.
import brand_ops.api.app  # noqa: F401  (import-order fix; see comment above)
from brand_ops import agent_identity, mcp_server

_REPO = Path(__file__).resolve().parents[1]
_MCPS_JSON = _REPO / ".well-known" / "mcps.json"


def test_module_imports_and_names_server() -> None:
    assert mcp_server.mcp.name == "brand-ops"


def test_list_brands_shape() -> None:
    result = mcp_server.list_brands()
    assert "error" not in result
    assert isinstance(result["brands"], list)
    assert result["count"] == len(result["brands"])
    # The seeded kit set includes magic-unicorn.
    assert "magic-unicorn" in result["brands"]


def test_list_asset_types_shape() -> None:
    result = mcp_server.list_asset_types()
    assert "error" not in result
    types = result["asset_types"]
    assert isinstance(types, list)
    assert result["count"] == len(types)

    keys = {t["key"] for t in types}
    assert "og_image" in keys
    assert "logo" in keys

    for spec in types:
        assert set(spec) == {"key", "label", "size", "default_backend", "transparent"}
        assert isinstance(spec["size"], list)
        assert len(spec["size"]) == 2
        assert all(isinstance(n, int) for n in spec["size"])
        assert isinstance(spec["transparent"], bool)


def test_get_brand_identity_shape() -> None:
    result = mcp_server.get_brand("magic-unicorn")
    assert "error" not in result
    assert result["slug"] == "magic-unicorn"
    assert result["display_name"] == "Magic Unicorn"
    assert result["colors"]["primary"].startswith("#")
    assert "primary_font" in result["fonts"]
    assert isinstance(result["style_keywords"], list)
    assert isinstance(result["voice"]["forbidden_phrases"], list)
    assert "declared" in result["references"]


def test_get_brand_unknown_returns_error_not_raise() -> None:
    result = mcp_server.get_brand("does-not-exist")
    assert "error" in result
    assert result["slug"] == "does-not-exist"


def test_build_prompt_og_image_magic_unicorn() -> None:
    result = mcp_server.build_prompt(
        "magic-unicorn",
        "og_image",
        topic="self-hostable AI infrastructure",
    )
    assert "error" not in result
    assert result["brand"] == "magic-unicorn"
    assert result["asset"] == "og_image"
    assert isinstance(result["prompt"], str)
    assert result["prompt"]  # non-empty composed prompt
    # og_image defaults to gemini at 1200x630 per the asset registry.
    assert result["backend"] == "gemini"
    assert result["size"] == [1200, 630]
    assert result["transparent"] is False
    assert isinstance(result["warnings"], list)
    assert isinstance(result["missing_references"], list)
    assert result["planned_output"].endswith(".png")


def test_build_prompt_unknown_asset_returns_error() -> None:
    result = mcp_server.build_prompt("magic-unicorn", "not_a_real_asset")
    assert "error" in result


def test_list_generated_assets_shape() -> None:
    result = mcp_server.list_generated_assets(brand="magic-unicorn")
    assert "error" not in result
    assert isinstance(result["assets"], list)
    assert result["count"] == len(result["assets"])


# ---------------------------------------------------------------------------
# Agent-identity tools
# ---------------------------------------------------------------------------


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    """A tmp kits dir seeded with a real brand.yaml (mirrors test_agent_identity)."""
    src = _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml"
    d = tmp_path / "magic-unicorn"
    d.mkdir()
    shutil.copy(src, d / "brand.yaml")
    return tmp_path


@pytest.fixture
def kits_pointed(monkeypatch: pytest.MonkeyPatch, kits: Path) -> Path:
    """Point the kits root at the tmp dir.

    The MCP agent tools call into agent_identity with no kits_dir, so they resolve
    through brand_loader.current_kits_dir(), which reads brand_loader.DEFAULT_KITS_DIR
    (unscoped here, so the flat tmp root). Patch that one source point.
    """
    from brand_ops import brand_loader

    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", kits)
    return kits


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (182, 110, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the avatar image backend so no network or spend is involved."""
    from brand_ops.backends.base import GenRequest, GenResult

    png = _png()

    class _Impl:
        def generate(self, req: GenRequest) -> GenResult:
            return GenResult(
                images=[png], mime="image/png", backend="litellm", model="gpt-image-1"
            )

    monkeypatch.setattr(
        agent_identity, "resolve_image_backend", lambda d, r, s: ("litellm", _Impl(), None)
    )


# The six agent-identity tools and their declared scope/tier/read_only posture.
_AGENT_TOOLS = {
    "list_brand_agents": ("brand-ops:read", "free", True),
    "get_agent_manifest": ("brand-ops:read", "free", True),
    "get_agent_team": ("brand-ops:read", "free", True),
    "create_agent": ("brand-ops:generate", "pro", False),
    "generate_agent_avatar": ("brand-ops:generate", "pro", False),
    "generate_agent_variants": ("brand-ops:generate", "pro", False),
}


def test_agent_tools_registered_with_fastmcp() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    for tool in _AGENT_TOOLS:
        assert tool in names, f"{tool} not registered with FastMCP"


def test_agent_tools_manifest_scope_tier_read_only() -> None:
    # The mcps.json manifest is the canonical source of scope/tier/read_only that
    # Brigade reads, so assert each new tool's entry matches the intended posture:
    # read tools are brand-ops:read / free / read_only; write tools are
    # brand-ops:generate / pro / not read_only.
    manifest = json.loads(_MCPS_JSON.read_text())
    entries = {t["name"]: t for t in manifest["tools"]}
    for tool, (scope, tier, read_only) in _AGENT_TOOLS.items():
        assert tool in entries, f"{tool} missing from mcps.json"
        entry = entries[tool]
        assert entry["scopes_required"] == [scope]
        assert entry["tier_required"] == tier
        assert entry["read_only"] is read_only


def test_list_brand_agents_empty_then_populated(
    monkeypatch: pytest.MonkeyPatch, kits_pointed: Path
) -> None:
    # No agents yet: an empty roster with a matching count.
    result = mcp_server.list_brand_agents("magic-unicorn")
    assert "error" not in result
    assert result["agents"] == []
    assert result["count"] == 0

    # Create one through the write tool, then it shows up in the listing.
    created = mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    assert "error" not in created
    assert created["slug"] == "iris"
    assert created["role"] == "brand director"
    assert created["brand"] == "magic-unicorn"
    assert created["voice_tone"]  # inherited from the brand's voice
    assert created["avatar_url"] is None  # no avatar yet

    populated = mcp_server.list_brand_agents("magic-unicorn")
    assert populated["count"] == 1
    assert populated["agents"][0]["slug"] == "iris"


def test_list_brand_agents_unknown_brand_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    result = mcp_server.list_brand_agents("does-not-exist")
    assert "error" in result
    assert result["brand"] == "does-not-exist"


def test_create_agent_duplicate_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    assert "error" not in mcp_server.create_agent("magic-unicorn", "Iris", "director")
    dup = mcp_server.create_agent("magic-unicorn", "Iris", "director")
    assert "error" in dup
    assert dup["name"] == "Iris"


def test_get_agent_team_shape_and_governance(
    monkeypatch: pytest.MonkeyPatch, kits_pointed: Path
) -> None:
    # Empty team: the shared Director, the brand Steward, no agents, zero tally.
    team = mcp_server.get_agent_team("magic-unicorn")
    assert "error" not in team
    assert team["brand"] == "magic-unicorn"
    assert team["brand_display_name"] == "Magic Unicorn"
    assert team["director"]["name"] == "Iris"
    assert team["director"]["kind"] == "director"
    assert team["steward"]["kind"] == "steward"
    assert team["agents"] == []
    assert team["governance"] == {
        "total": 0,
        "on_brand": 0,
        "needs_review": 0,
        "unrated": 0,
    }

    # Create an agent and generate its (stubbed) avatar: the team now counts one
    # agent, and a passing/skipped avatar lands it in the on_brand bucket.
    mcp_server.create_agent("magic-unicorn", "Atlas", "specialist")
    _stub_backend(monkeypatch)
    avatar = mcp_server.generate_agent_avatar("magic-unicorn", "atlas")
    assert "error" not in avatar
    assert avatar["avatar_url"].endswith("/brands/magic-unicorn/agents/atlas/avatar")

    team2 = mcp_server.get_agent_team("magic-unicorn")
    gov = team2["governance"]
    assert gov["total"] == 1
    assert len(team2["agents"]) == 1
    assert gov["on_brand"] + gov["needs_review"] + gov["unrated"] == 1


def test_get_agent_team_unknown_brand_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    result = mcp_server.get_agent_team("no-such-brand")
    assert "error" in result
    assert result["brand"] == "no-such-brand"


def test_get_agent_manifest_after_create(
    kits_pointed: Path,
) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director", "bio here")
    manifest = mcp_server.get_agent_manifest("magic-unicorn", "iris")
    assert "error" not in manifest
    assert manifest["schema"].startswith("brand-ops/agent-identity")
    assert manifest["name"] == "Iris"
    assert manifest["brand"] == "magic-unicorn"
    assert manifest["bio"] == "bio here"
    assert "primary" in manifest["palette"] and "accent" in manifest["palette"]


def test_get_agent_manifest_unknown_agent_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    result = mcp_server.get_agent_manifest("magic-unicorn", "ghost")
    assert "error" in result
    assert result["agent"] == "ghost"


def test_generate_agent_variants_stubbed(
    monkeypatch: pytest.MonkeyPatch, kits_pointed: Path
) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    _stub_backend(monkeypatch)
    ident = mcp_server.generate_agent_variants("magic-unicorn", "iris")
    assert "error" not in ident
    assert set(ident["variants"]) == {"primary", "thinking", "success", "alert"}
    agent_dir = kits_pointed / "magic-unicorn" / "agents" / "iris"
    assert (agent_dir / "avatar.png").is_file()
    for state in ("thinking", "success", "alert"):
        assert (agent_dir / f"avatar-{state}.png").is_file()


# ---------------------------------------------------------------------------
# generate_brand_kit (the batch generate tool)
# ---------------------------------------------------------------------------


def _stub_kit_engine(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the engine so generate_brand_kit generates with no network or spend.

    Routes the real engine.generate_asset (so prompt-building, the asset registry,
    and the MCP generate_asset tool's contract are all exercised) but swaps the
    image backend for a healthy fake and neutralizes the on-disk write. Records the
    asset types the backend was asked to produce, in order, and returns that list.
    """
    from brand_ops import engine
    from brand_ops.backends.base import GenRequest, GenResult

    called: list[str] = []

    class _Impl:
        def health(self) -> bool:
            return True

        def generate(self, req: GenRequest) -> GenResult:
            return GenResult(
                images=[b"PNG"], mime="image/png", backend="litellm", model="m"
            )

    monkeypatch.setattr(engine, "load_backend", lambda name, settings: _Impl())

    def _save(brand_slug, asset_type, variant, built, result, *a, **k):
        called.append(asset_type)
        # A deterministic fake path so the kit can surface file + url without
        # touching disk; nothing is written.
        return [Path(f"/tmp/{brand_slug}/generated/{asset_type}/img.png")]

    monkeypatch.setattr(engine, "save_result", _save)
    return called


def test_generate_brand_kit_registered_with_fastmcp() -> None:
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert "generate_brand_kit" in names


def test_generate_brand_kit_manifest_scope_tier_read_only() -> None:
    manifest = json.loads(_MCPS_JSON.read_text())
    entries = {t["name"]: t for t in manifest["tools"]}
    assert "generate_brand_kit" in entries, "generate_brand_kit missing from mcps.json"
    entry = entries["generate_brand_kit"]
    assert entry["scopes_required"] == ["brand-ops:generate"]
    assert entry["tier_required"] == "pro"
    assert entry["read_only"] is False


def test_generate_brand_kit_only_missing_generates_all_six(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No covering assets anywhere: only_missing must target every registry type.
    monkeypatch.setattr(mcp_server, "read_assets", lambda **kw: [])
    called = _stub_kit_engine(monkeypatch)

    result = mcp_server.generate_brand_kit("magic-unicorn")  # only_missing default True

    assert "error" not in result
    assert result["brand"] == "magic-unicorn"
    assert result["generated"] == len(mcp_server.ASSET_TYPES)
    assert result["failed"] == 0
    produced = {r["asset_type"] for r in result["results"]}
    assert produced == set(mcp_server.ASSET_TYPES)
    assert all(r["status"] == "ok" for r in result["results"])
    # Every targeted type was actually generated, none skipped.
    assert set(called) == set(mcp_server.ASSET_TYPES)
    # Each ok result carries a servable file + url for the written image.
    for r in result["results"]:
        assert r["file"]
        assert r["url"].startswith("/api/assets/file?")


def test_generate_brand_kit_explicit_types_generates_exactly_those(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An explicit list is honored verbatim and overrides only_missing entirely.
    monkeypatch.setattr(mcp_server, "read_assets", lambda **kw: [])
    called = _stub_kit_engine(monkeypatch)

    result = mcp_server.generate_brand_kit(
        "magic-unicorn", only_missing=True, asset_types=["logo", "og_image"]
    )

    assert "error" not in result
    assert result["generated"] == 2
    assert result["failed"] == 0
    assert [r["asset_type"] for r in result["results"]] == ["logo", "og_image"]
    assert called == ["logo", "og_image"]


def test_generate_brand_kit_unknown_explicit_type_returns_error_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "read_assets", lambda **kw: [])
    _stub_kit_engine(monkeypatch)
    result = mcp_server.generate_brand_kit(
        "magic-unicorn", asset_types=["logo", "not_a_real_asset"]
    )
    assert "error" in result
    assert result["brand"] == "magic-unicorn"


def test_generate_brand_kit_per_type_error_is_captured_not_aborting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One type's backend blows up; the kit must capture it as status "error" and
    # still generate every other type (no abort, nothing raised).
    from brand_ops import engine
    from brand_ops.backends.base import BackendError

    monkeypatch.setattr(mcp_server, "read_assets", lambda **kw: [])

    real_generate = engine.generate_asset

    def _flaky(brand_slug, asset_type, **kwargs):
        if asset_type == "logo" and not kwargs.get("dry_run"):
            raise BackendError("backend exploded")
        return real_generate(brand_slug, asset_type, **kwargs)

    # Stub the backend + writer for the types that do succeed, then route through
    # the flaky engine wrapper.
    _stub_kit_engine(monkeypatch)
    monkeypatch.setattr(engine, "generate_asset", _flaky)

    result = mcp_server.generate_brand_kit("magic-unicorn")

    assert "error" not in result  # the kit itself never errors
    by_type = {r["asset_type"]: r for r in result["results"]}
    assert by_type["logo"]["status"] == "error"
    assert "exploded" in by_type["logo"]["error"]
    # Every other registry type still came out ok.
    for key in mcp_server.ASSET_TYPES:
        if key == "logo":
            continue
        assert by_type[key]["status"] == "ok"
    assert result["failed"] == 1
    assert result["generated"] == len(mcp_server.ASSET_TYPES) - 1


# ---------------------------------------------------------------------------
# Parity tools (the 13 added to reach full GUI/REST parity) + annotations,
# resource templates, and prompts. Each new tool is asserted registered and a
# representative call is exercised against the fixtures, never touching the
# network or live generation.
# ---------------------------------------------------------------------------

# The new tools and the read-only posture their annotations must advertise. The
# reads are readOnlyHint True; the spend tools are destructiveHint True; the other
# writes are not read-only and not destructive.
_NEW_TOOLS_READ_ONLY = {
    "list_agents": True,
    "run_agent": False,
    "plan_generation": False,
    "director_chat": False,
    "approve_asset": False,
    "transition_asset": False,
    "upload_asset": False,
    "list_voices": True,
    "update_agent": False,
    "set_agent_voice": False,
    "read_activity": True,
    "get_brand_compliance": True,
    "fetch_asset_bytes": True,
}

# The tools whose annotations mark a metered-gateway spend (destructiveHint). The
# four image-generating writes; the parity additions never spend (generate_card
# composes locally, the rest are reads/metadata writes).
_SPEND_TOOLS = {
    "generate_asset",
    "generate_brand_kit",
    "generate_agent_avatar",
    "generate_agent_variants",
}


def _live_tools() -> dict:
    """The FastMCP-registered tools by name (each carrying its annotations)."""
    return {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}


def test_new_parity_tools_registered_with_fastmcp() -> None:
    names = set(_live_tools())
    for tool in _NEW_TOOLS_READ_ONLY:
        assert tool in names, f"{tool} not registered with FastMCP"


def test_every_tool_carries_annotations_with_a_title() -> None:
    # Best-in-breed: every tool (the original 22 + the new ones) advertises
    # ToolAnnotations with a human title, so a client can render and reason about
    # each tool without fetching its full schema.
    for name, tool in _live_tools().items():
        assert tool.annotations is not None, f"{name} has no annotations"
        assert tool.annotations.title, f"{name} annotation has no title"


def test_read_tools_are_marked_read_only() -> None:
    tools = _live_tools()
    for name, read_only in _NEW_TOOLS_READ_ONLY.items():
        assert tools[name].annotations.readOnlyHint is read_only, name


def test_spend_tools_are_marked_destructive_and_not_idempotent() -> None:
    # The metered-gateway tools warn a client that a call may incur spend and that
    # a retry is another charge: destructiveHint True, idempotentHint False.
    tools = _live_tools()
    for name in _SPEND_TOOLS:
        ann = tools[name].annotations
        assert ann.destructiveHint is True, f"{name} should be destructive (spend)"
        assert ann.idempotentHint is False, f"{name} should be non-idempotent (spend)"
    # generate_card composes locally with no provider spend, so it is a plain
    # write, not destructive.
    assert tools["generate_card"].annotations.destructiveHint is False


def test_new_parity_tools_listed_in_mcps_json() -> None:
    # The canonical discovery manifest advertises every new tool with its posture.
    manifest = json.loads(_MCPS_JSON.read_text())
    entries = {t["name"]: t for t in manifest["tools"]}
    for tool, read_only in _NEW_TOOLS_READ_ONLY.items():
        assert tool in entries, f"{tool} missing from mcps.json"
        assert entries[tool]["read_only"] is read_only, tool
        # Reads are free/read; writes are pro/generate.
        if read_only:
            assert entries[tool]["scopes_required"] == ["brand-ops:read"]
            assert entries[tool]["tier_required"] == "free"
        else:
            assert entries[tool]["scopes_required"] == ["brand-ops:generate"]
            assert entries[tool]["tier_required"] == "pro"


def test_mcps_json_advertises_resource_templates_and_prompts() -> None:
    manifest = json.loads(_MCPS_JSON.read_text())
    template_uris = {t["uri_template"] for t in manifest["resource_templates"]}
    assert "brand-ops://brand/{slug}/guidelines" in template_uris
    assert "brand-ops://brand/{slug}/assets" in template_uris
    assert "brand-ops://brand/{slug}/tokens" in template_uris
    assert "brand-ops://brand/{slug}/agents" in template_uris
    prompt_names = {p["name"] for p in manifest["prompts"]}
    assert prompt_names == {
        "launch_full_brand_kit",
        "audit_brand_compliance",
        "stand_up_steward_agent",
    }


def test_mcps_json_tool_set_matches_live_server() -> None:
    # The static manifest must list exactly the tools the server registers, so a
    # consumer reading the manifest sees the real surface (no drift).
    manifest = json.loads(_MCPS_JSON.read_text())
    file_tools = {t["name"] for t in manifest["tools"]}
    live_tools = set(_live_tools())
    assert file_tools == live_tools


def test_resource_templates_and_prompts_registered() -> None:
    templates = asyncio.run(mcp_server.mcp.list_resource_templates())
    uris = {t.uriTemplate for t in templates}
    assert "brand-ops://brand/{slug}/guidelines" in uris
    assert "brand-ops://brand/{slug}/assets" in uris
    assert "brand-ops://brand/{slug}/tokens" in uris
    assert "brand-ops://brand/{slug}/agents" in uris
    prompts = asyncio.run(mcp_server.mcp.list_prompts())
    assert {p.name for p in prompts} == {
        "launch_full_brand_kit",
        "audit_brand_compliance",
        "stand_up_steward_agent",
    }


# ---- list_agents / run_agent (the 8 specialists) --------------------------


def test_list_agents_returns_the_specialists() -> None:
    result = mcp_server.list_agents()
    assert "error" not in result
    from brand_ops.agents.registry import AGENT_REGISTRY

    names = {a["name"] for a in result["agents"]}
    assert names == {spec.name for spec in AGENT_REGISTRY}
    assert result["count"] == len(result["agents"])
    for agent in result["agents"]:
        assert agent["name"] and agent["description"]
        assert isinstance(agent["input_schema"], dict)
        assert isinstance(agent["scopes_required"], list)


def test_run_agent_refine_is_deterministic_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # refine merges the critic's prompt_tweaks into the base prompt with no LLM, so
    # it is fixed and reproducible offline. Clear any gateway env so it cannot reach
    # a live model.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = asyncio.run(
        mcp_server.run_agent(
            "refine",
            {
                "asset": {"prompt": "a teal logo"},
                "critique": {"prompt_tweaks": ["add a deep navy background"]},
            },
        )
    )
    assert "error" not in result
    assert result["agent"] == "refine"
    assert result["status"] == "ok"
    refined = result["result"]["refined_prompt"]
    assert "teal logo" in refined and "deep navy background" in refined


def test_run_agent_unknown_name_returns_error_not_raise() -> None:
    result = asyncio.run(mcp_server.run_agent("not_an_agent", {}))
    assert "error" in result
    assert result["agent"] == "not_an_agent"


# ---- plan_generation ------------------------------------------------------


def test_plan_generation_returns_a_valid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # No gateway: the planner falls back to its deterministic heuristic and still
    # returns a valid request over the real seeded brands (magic-unicorn exists).
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = asyncio.run(
        mcp_server.plan_generation("make an OG image for the launch", brand="magic-unicorn")
    )
    assert "error" not in result
    request = result["request"]
    assert request["brand"] and request["asset"]
    assert isinstance(result.get("rationale", []), list)


# ---- director_chat --------------------------------------------------------


def test_director_chat_degrades_to_a_reply_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no gateway the Director returns a graceful in-persona chat reply rather
    # than raising; the tool surfaces that reply.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = asyncio.run(
        mcp_server.director_chat("hello there", brand="magic-unicorn")
    )
    assert "error" not in result
    assert isinstance(result["reply"], str) and result["reply"]
    assert result["intent"]  # some routed intent (chat on the offline path)


# ---- list_voices ----------------------------------------------------------


def test_list_voices_returns_the_catalog() -> None:
    result = mcp_server.list_voices()
    assert "error" not in result
    assert isinstance(result["voices"], list) and result["voices"]
    assert result["count"] == len(result["voices"])
    assert "preview_available" in result
    # Every voice carries the picker fields.
    for v in result["voices"]:
        assert v["id"] and v["label"] and v["engine"]


# ---- update_agent / set_agent_voice (need a tmp kits dir + an agent) -------


def test_update_agent_changes_role_and_bio(kits_pointed: Path) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director", "first bio")
    updated = mcp_server.update_agent(
        "magic-unicorn", "iris", role="brand steward", bio="new bio"
    )
    assert "error" not in updated
    assert updated["role"] == "brand steward"
    assert updated["bio"] == "new bio"


def test_update_agent_empty_role_returns_error_not_raise(kits_pointed: Path) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    result = mcp_server.update_agent("magic-unicorn", "iris", role="   ")
    assert "error" in result


def test_update_agent_emits_activity_event(kits_pointed: Path) -> None:
    # Parity with PATCH /api/brands/{slug}/agents/{agent}: an MCP persona edit
    # lands on the provenance feed too, so both doors leave the same audit trail.
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    mcp_server.update_agent("magic-unicorn", "iris", bio="updated")
    feed = mcp_server.read_activity(brand="magic-unicorn")
    actions = [e["action"] for e in feed["events"]]
    assert "updated agent" in actions


def test_set_agent_voice_emits_activity_event(kits_pointed: Path) -> None:
    # Parity with PUT .../voice: binding and clearing each land on the feed.
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    mcp_server.set_agent_voice("magic-unicorn", "iris", "af_bella")
    mcp_server.set_agent_voice("magic-unicorn", "iris", None)
    actions = [e["action"] for e in mcp_server.read_activity(brand="magic-unicorn")["events"]]
    assert "set agent voice" in actions
    assert "cleared agent voice" in actions


def test_set_agent_voice_binds_and_clears(kits_pointed: Path) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    # A known catalog voice binds.
    bound = mcp_server.set_agent_voice("magic-unicorn", "iris", "af_bella")
    assert "error" not in bound
    assert bound["voice"]["voice_id"] == "af_bella"
    # A null clears it: the binding is removed, so the voice object is None.
    cleared = mcp_server.set_agent_voice("magic-unicorn", "iris", None)
    assert "error" not in cleared
    assert cleared["voice"] is None


def test_set_agent_voice_unknown_voice_returns_error_not_raise(kits_pointed: Path) -> None:
    mcp_server.create_agent("magic-unicorn", "Iris", "brand director")
    result = mcp_server.set_agent_voice("magic-unicorn", "iris", "not-a-voice")
    assert "error" in result


# ---- read_activity --------------------------------------------------------


def test_read_activity_reads_the_feed(kits_pointed: Path) -> None:
    # An empty feed reads as an empty list; after an append it carries the event.
    from brand_ops.activity import append_event

    empty = mcp_server.read_activity()
    assert "error" not in empty
    assert empty["events"] == []
    assert empty["count"] == 0

    append_event(
        "you", "human", "approved", brand="magic-unicorn", target="logo/x.png",
        ts="2026-01-01T00:00:00Z", kits_dir=kits_pointed,
    )
    feed = mcp_server.read_activity(brand="magic-unicorn")
    assert feed["count"] == 1
    assert feed["events"][0]["action"] == "approved"


def test_read_activity_clamps_limit(kits_pointed: Path) -> None:
    # An out-of-range limit is clamped rather than raising; here it just reads empty.
    assert mcp_server.read_activity(limit=99999)["count"] == 0
    assert mcp_server.read_activity(limit=-5)["count"] == 0


# ---- get_brand_compliance -------------------------------------------------


def test_get_brand_compliance_rolls_up(kits_pointed: Path) -> None:
    # A brand with no catalog assets yet rolls up to an all-zero summary, never an
    # error, through the same engine the REST route uses.
    result = mcp_server.get_brand_compliance("magic-unicorn")
    assert "error" not in result
    assert result["brand"] == "magic-unicorn"
    assert result["summary"]["total"] == 0
    assert result["assets"] == []


def test_get_brand_compliance_unknown_brand_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    result = mcp_server.get_brand_compliance("no-such-brand")
    assert "error" in result
    assert result["brand"] == "no-such-brand"


# ---- asset lifecycle: a seeded generated asset, then transition/approve ----


def _seed_generated_asset(kits_root: Path, *, brand: str = "magic-unicorn") -> str:
    """Write one generated PNG + sidecar under the tmp kits tree; return its name.

    Mirrors what the engine's output manager writes so the lifecycle tools resolve
    a real catalog asset. lifecycle_status starts None (never stamped).
    """
    asset_dir = kits_root / brand / "generated" / "logo"
    asset_dir.mkdir(parents=True, exist_ok=True)
    name = "logo-test.png"
    (asset_dir / name).write_bytes(_png())
    sidecar = {
        "brand": brand,
        "asset_type": "logo",
        "kind": "generated",
        "variant": None,
        "backend": "litellm",
        "model": "m",
        "mime": "image/png",
        "size": [64, 64],
        "transparent": True,
        "cost_cents": 0.0,
        "created_at": "2026-01-01-00-00-00",
        "prompt": "a test logo",
        "reference_images": [],
        "lifecycle_status": None,
    }
    (asset_dir / (name + ".json")).write_text(json.dumps(sidecar, indent=2))
    return name


def test_transition_asset_moves_lifecycle(kits_pointed: Path) -> None:
    name = _seed_generated_asset(kits_pointed)
    result = mcp_server.transition_asset(
        "magic-unicorn", "logo", name, to="changes_requested", note="too dark"
    )
    assert "error" not in result
    assert result["asset"]["lifecycle_status"] == "changes_requested"
    assert result["asset"]["file"] == name


def test_approve_asset_flips_to_approved(kits_pointed: Path) -> None:
    name = _seed_generated_asset(kits_pointed)
    result = mcp_server.approve_asset("magic-unicorn", "logo", name)
    assert "error" not in result
    assert result["asset"]["lifecycle_status"] == "approved"


def test_transition_asset_unknown_kind_returns_error_not_raise(kits_pointed: Path) -> None:
    result = mcp_server.transition_asset(
        "magic-unicorn", "logo", "x.png", to="approved", kind="bogus"
    )
    assert "error" in result


def test_transition_asset_missing_sidecar_returns_error_not_raise(
    kits_pointed: Path,
) -> None:
    result = mcp_server.transition_asset(
        "magic-unicorn", "logo", "ghost.png", to="approved"
    )
    assert "error" in result


def test_list_generated_assets_status_filter(kits_pointed: Path) -> None:
    # Seed two assets, approve one, then the status filter returns only the match.
    a = _seed_generated_asset(kits_pointed)
    asset_dir = kits_pointed / "magic-unicorn" / "generated" / "logo"
    # A second asset, left at null lifecycle (never stamped).
    (asset_dir / "logo-two.png").write_bytes(_png())
    (asset_dir / "logo-two.png.json").write_text(
        json.dumps({"brand": "magic-unicorn", "asset_type": "logo", "lifecycle_status": None})
    )
    mcp_server.approve_asset("magic-unicorn", "logo", a)

    approved = mcp_server.list_generated_assets(brand="magic-unicorn", status="approved")
    assert "error" not in approved
    # The raw catalog records carry the image path; match on its basename.
    files = {Path(r["image_path"]).name for r in approved["assets"]}
    assert a in files and "logo-two.png" not in files

    # A bad status value is a structured error, not a raise.
    assert "error" in mcp_server.list_generated_assets(status="bogus")


# ---- upload_asset (base64 + path) -----------------------------------------


def test_upload_asset_base64_lands_in_imported(kits_pointed: Path) -> None:
    import base64 as _b64

    encoded = _b64.b64encode(_png()).decode()
    result = mcp_server.upload_asset(
        "magic-unicorn",
        "logo",
        file_base64=encoded,
        filename="dropped.png",
        content_type="image/png",
    )
    assert "error" not in result
    rec = result["asset"]
    assert rec["kind"] == "imported"
    assert rec["asset_type"] == "logo"
    # The bytes actually landed under imported/logo/.
    assert (kits_pointed / "magic-unicorn" / "imported" / "logo" / "dropped.png").is_file()


def test_upload_asset_from_path(kits_pointed: Path, tmp_path: Path) -> None:
    src = tmp_path / "src-logo.png"
    src.write_bytes(_png())
    result = mcp_server.upload_asset("magic-unicorn", "logo", file_path=str(src))
    assert "error" not in result
    assert result["asset"]["kind"] == "imported"


def test_upload_asset_requires_exactly_one_source(kits_pointed: Path) -> None:
    # Neither source.
    assert "error" in mcp_server.upload_asset("magic-unicorn", "logo")
    # Both sources.
    both = mcp_server.upload_asset(
        "magic-unicorn", "logo", file_path="/x.png", file_base64="AAAA", filename="x.png"
    )
    assert "error" in both


def test_upload_asset_unknown_brand_returns_error_not_raise(kits_pointed: Path) -> None:
    import base64 as _b64

    result = mcp_server.upload_asset(
        "no-such-brand", "logo", file_base64=_b64.b64encode(_png()).decode(), filename="x.png"
    )
    assert "error" in result
    # The brand gate runs first (mirroring the REST route), so the error is about
    # the brand, never a size/decode message about the bytes.
    assert "larger than" not in result["error"]


def test_upload_asset_accepts_newline_wrapped_base64(kits_pointed: Path) -> None:
    # The standard `base64` CLI and base64.encodebytes emit RFC-2045 line-wrapped
    # base64 (76-char lines with newlines). The tool must decode that, not only
    # single-line base64.
    import base64 as _b64

    wrapped = _b64.encodebytes(_png()).decode()  # carries embedded newlines
    assert "\n" in wrapped
    result = mcp_server.upload_asset(
        "magic-unicorn", "logo", file_base64=wrapped, filename="wrapped.png"
    )
    assert "error" not in result
    assert result["asset"]["kind"] == "imported"


# ---- fetch_asset_bytes (returns MCP image content) ------------------------


def test_fetch_asset_bytes_returns_image_content(kits_pointed: Path) -> None:
    from mcp.server.fastmcp import Image

    name = _seed_generated_asset(kits_pointed)
    result = mcp_server.fetch_asset_bytes("magic-unicorn", "logo", name)
    assert isinstance(result, Image), "raster asset should come back as MCP image content"


def test_fetch_asset_bytes_missing_returns_error_not_raise(kits_pointed: Path) -> None:
    result = mcp_server.fetch_asset_bytes("magic-unicorn", "logo", "ghost.png")
    assert isinstance(result, dict) and "error" in result


def test_fetch_asset_bytes_non_raster_returns_error_with_path(kits_pointed: Path) -> None:
    # An SVG is not raster image content: a structured error that points at the path.
    asset_dir = kits_pointed / "magic-unicorn" / "generated" / "logo"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "vector.svg").write_text("<svg/>")
    result = mcp_server.fetch_asset_bytes("magic-unicorn", "logo", "vector.svg")
    assert isinstance(result, dict) and "error" in result
    assert result.get("path", "").endswith("vector.svg")
