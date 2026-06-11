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
    """Point agent_identity at the tmp kits dir.

    The MCP agent tools call into agent_identity with no kits_dir, so they resolve
    through DEFAULT_KITS_DIR (imported by value); repoint it at the tmp tree.
    """
    monkeypatch.setattr(agent_identity, "DEFAULT_KITS_DIR", kits)
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
