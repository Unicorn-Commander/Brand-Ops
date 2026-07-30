"""Agent-identity tests: create / list / avatar (stubbed backend) / manifest.

Uses a tmp kits dir seeded with a real brand.yaml so brand loading + voice
inheritance are exercised, and stubs the image backend so no network is touched.
"""
from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from brand_ops import agent_identity
from brand_ops.api.app import create_app
from brand_ops.api.schemas import AgentIdentityOut, AgentTeam
from brand_ops.backends.base import GenRequest, GenResult
from brand_ops.brand_loader import BrandNotFound

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    src = _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml"
    d = tmp_path / "magic-unicorn"
    d.mkdir()
    shutil.copy(src, d / "brand.yaml")
    return tmp_path


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (182, 110, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_create_and_list(kits: Path) -> None:
    ident = agent_identity.create_agent(
        "magic-unicorn", "Iris", "brand director", "Leads brand ops", kits_dir=kits
    )
    assert ident["slug"] == "iris"
    assert ident["role"] == "brand director"
    assert ident["brand"] == "magic-unicorn"
    assert ident["voice_tone"]  # inherited from the brand's voice
    assert ident["avatar_url"] is None  # no avatar yet

    agents = agent_identity.list_agents("magic-unicorn", kits_dir=kits)
    assert any(a["slug"] == "iris" for a in agents)


def test_duplicate_is_conflict(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    with pytest.raises(FileExistsError):
        agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)


def test_blank_name_rejected(kits: Path) -> None:
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.create_agent("magic-unicorn", "", "director", kits_dir=kits)


def test_generate_avatar_stubbed(monkeypatch: pytest.MonkeyPatch, kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "brand director", kits_dir=kits)
    png = _png()

    class _Impl:
        def generate(self, req: GenRequest) -> GenResult:
            return GenResult(images=[png], mime="image/png", backend="litellm", model="gpt-image-1")

    monkeypatch.setattr(
        agent_identity, "resolve_image_backend", lambda d, r, s: ("litellm", _Impl(), None)
    )
    ident = agent_identity.generate_avatar("magic-unicorn", "iris", kits_dir=kits)

    assert ident["avatar_file"] == "avatar.png"
    assert ident["avatar_url"].endswith("/brands/magic-unicorn/agents/iris/avatar")
    assert (kits / "magic-unicorn" / "agents" / "iris" / "avatar.png").is_file()
    # compliance was attempted (a dict with a status), not fatal
    assert ident["compliance"] is None or "status" in ident["compliance"]


def test_manifest_shape(kits: Path) -> None:
    agent_identity.create_agent(
        "magic-unicorn", "Iris", "brand director", "bio here", kits_dir=kits
    )
    m = agent_identity.build_manifest("magic-unicorn", "iris", kits_dir=kits)
    assert m["schema"].startswith("brand-ops/agent-identity")
    assert m["name"] == "Iris"
    assert m["brand"] == "magic-unicorn"
    assert m["bio"] == "bio here"
    assert "primary" in m["palette"] and "accent" in m["palette"]
    assert "tone" in m["voice"]


def test_avatar_path_bad_slug(kits: Path) -> None:
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.avatar_path("magic-unicorn", "../evil", kits_dir=kits)


def _stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _png()

    class _Impl:
        def generate(self, req: GenRequest) -> GenResult:
            return GenResult(
                images=[png], mime="image/png", backend="litellm", model="gpt-image-1"
            )

    monkeypatch.setattr(
        agent_identity, "resolve_image_backend", lambda d, r, s: ("litellm", _Impl(), None)
    )


def test_generate_variants_stubbed(monkeypatch: pytest.MonkeyPatch, kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "brand director", kits_dir=kits)
    _stub_backend(monkeypatch)

    ident = agent_identity.generate_variants("magic-unicorn", "iris", kits_dir=kits)

    agent_dir = kits / "magic-unicorn" / "agents" / "iris"
    # The primary is avatar.png; the rest are avatar-<state>.png.
    assert (agent_dir / "avatar.png").is_file()
    for state in ("thinking", "success", "alert"):
        assert (agent_dir / f"avatar-{state}.png").is_file()

    # agent.json carries a variants map with all 4 states -> filenames.
    data = json.loads((agent_dir / "agent.json").read_text())
    assert data["variants"] == {
        "primary": "avatar.png",
        "thinking": "avatar-thinking.png",
        "success": "avatar-success.png",
        "alert": "avatar-alert.png",
    }
    assert data["avatar_file"] == "avatar.png"

    # The identity dict surfaces 4 variant URLs, one per state.
    variants = ident["variants"]
    assert set(variants) == {"primary", "thinking", "success", "alert"}
    for state, url in variants.items():
        assert url == f"/api/brands/magic-unicorn/agents/iris/variants/{state}"

    # And it round-trips through the wire schema unchanged.
    out = AgentIdentityOut(**ident)
    assert len(out.variants) == 4


def test_generate_variants_manifest_at_v2(
    monkeypatch: pytest.MonkeyPatch, kits: Path
) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "brand director", kits_dir=kits)
    _stub_backend(monkeypatch)
    agent_identity.generate_variants("magic-unicorn", "iris", kits_dir=kits)

    m = agent_identity.build_manifest("magic-unicorn", "iris", kits_dir=kits)
    assert m["schema"] == "brand-ops/agent-identity@2"
    avatars = m["avatars"]
    assert avatars["primary"] == "/api/brands/magic-unicorn/agents/iris/variants/primary"
    # avatars.variants holds the non-primary states (4 total states, 3 here).
    assert set(avatars["variants"]) == {"thinking", "success", "alert"}
    # All existing manifest keys are preserved.
    assert m["name"] == "Iris"
    assert "primary" in m["palette"] and "accent" in m["palette"]
    assert "tone" in m["voice"]


def test_variant_path_bad_slug(kits: Path) -> None:
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.variant_path("magic-unicorn", "../evil", "primary", kits_dir=kits)


def test_variant_path_unknown_state(kits: Path) -> None:
    agent_identity.create_agent("magic-unicorn", "Iris", "director", kits_dir=kits)
    with pytest.raises(agent_identity.AgentIdentityError):
        agent_identity.variant_path("magic-unicorn", "iris", "bogus", kits_dir=kits)


# ---------------------------------------------------------------------------
# Governance status + team rollup (Slice 3)
# ---------------------------------------------------------------------------


def _set_compliance(kits: Path, agent_slug: str, status: str | None) -> None:
    """Write a compliance verdict (or clear it) onto a created agent's agent.json."""
    meta = kits / "magic-unicorn" / "agents" / agent_slug / "agent.json"
    data = json.loads(meta.read_text())
    data["compliance"] = None if status is None else {"status": status, "score": 0}
    meta.write_text(json.dumps(data, indent=2))


def test_governance_status_buckets() -> None:
    # pass / warn / skipped all read as on_brand; fail is needs_review; absent or
    # malformed compliance is unrated.
    assert agent_identity.governance_status({"status": "pass"}) == "on_brand"
    assert agent_identity.governance_status({"status": "warn"}) == "on_brand"
    assert agent_identity.governance_status({"status": "skipped"}) == "on_brand"
    assert agent_identity.governance_status({"status": "fail"}) == "needs_review"
    assert agent_identity.governance_status(None) == "unrated"
    assert agent_identity.governance_status({}) == "unrated"


def test_identity_out_carries_governance_status(kits: Path) -> None:
    # A freshly created agent (no avatar) is unrated, and that survives the wire
    # schema; flipping the stored compliance to a fail flips the badge.
    agent_identity.create_agent("magic-unicorn", "Iris", "brand director", kits_dir=kits)
    ident = agent_identity.load_agent("magic-unicorn", "iris", kits_dir=kits)
    assert ident["governance_status"] == "unrated"
    assert AgentIdentityOut(**ident).governance_status == "unrated"

    _set_compliance(kits, "iris", "fail")
    flagged = agent_identity.load_agent("magic-unicorn", "iris", kits_dir=kits)
    assert flagged["governance_status"] == "needs_review"
    assert AgentIdentityOut(**flagged).governance_status == "needs_review"


def test_build_team_shape_and_director_steward(kits: Path) -> None:
    team = agent_identity.build_team("magic-unicorn", kits_dir=kits)

    assert team["brand"] == "magic-unicorn"
    assert team["brand_display_name"] == "Magic Unicorn"

    director = team["director"]
    assert director["name"] == "Iris"
    assert director["kind"] == "director"
    assert director["role"] == "brand director"
    assert director["avatar_url"] is None

    steward = team["steward"]
    assert steward["name"] == "Magic Unicorn"  # the brand display name
    assert steward["kind"] == "steward"
    # The steward's avatar mirrors the brand's canonical mark URL.
    assert steward["avatar_url"] == agent_identity._brand_mark_url(
        agent_identity.load_brand("magic-unicorn", kits)
    )
    assert steward["avatar_url"].startswith("/api/assets/file?brand=magic-unicorn")

    # No created agents yet: an empty roster and an all-zero governance tally.
    assert team["agents"] == []
    assert team["governance"] == {
        "total": 0,
        "on_brand": 0,
        "needs_review": 0,
        "unrated": 0,
    }

    # And the whole thing round-trips through the wire schema.
    AgentTeam.model_validate(team)


def test_build_team_governance_counts_move_on_fail_vs_pass(kits: Path) -> None:
    # Three created agents: one unrated (no avatar), one on-brand (pass), one
    # flagged (fail). The tally must split them and always sum to total.
    for name in ("Iris", "Atlas", "Vera"):
        agent_identity.create_agent("magic-unicorn", name, "specialist", kits_dir=kits)
    _set_compliance(kits, "atlas", "pass")
    _set_compliance(kits, "vera", "fail")
    # iris is left with no compliance -> unrated.

    team = agent_identity.build_team("magic-unicorn", kits_dir=kits)
    gov = team["governance"]
    assert gov["total"] == 3
    assert gov["on_brand"] == 1
    assert gov["needs_review"] == 1
    assert gov["unrated"] == 1
    assert gov["on_brand"] + gov["needs_review"] + gov["unrated"] == gov["total"]
    assert len(team["agents"]) == 3

    # Flip the flagged agent to a pass: it moves out of needs_review into on_brand.
    _set_compliance(kits, "vera", "pass")
    team2 = agent_identity.build_team("magic-unicorn", kits_dir=kits)
    gov2 = team2["governance"]
    assert gov2["on_brand"] == 2
    assert gov2["needs_review"] == 0
    assert gov2["unrated"] == 1


def test_build_team_unknown_brand_raises(kits: Path) -> None:
    # The route maps this to a 404 (see get_brand_agent_team).
    with pytest.raises(BrandNotFound):
        agent_identity.build_team("no-such-brand", kits_dir=kits)


# ---------------------------------------------------------------------------
# Avatar size derivatives over the HTTP surface (Slice 3, Part A)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_kits(monkeypatch: pytest.MonkeyPatch, kits: Path) -> Path:
    """Point the whole app (loader, agent_identity, routes) at the tmp kits dir.

    DEFAULT_KITS_DIR is a module constant each of these imported by value, so the
    routes resolve avatars under the tmp tree instead of the repo's brand-kits.
    """
    from brand_ops import brand_loader

    # Every module resolves its kits root through brand_loader.current_kits_dir(),
    # which reads brand_loader.DEFAULT_KITS_DIR, so patching the one source point
    # redirects them all (unscoped, so current_kits_dir() is the flat tmp root).
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", kits)
    return kits


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch, api_kits: Path
) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    return TestClient(create_app())


def _seed_avatar(monkeypatch: pytest.MonkeyPatch, kits: Path) -> None:
    """Create an agent and generate its (stubbed) avatar into the tmp kits tree."""
    agent_identity.create_agent("magic-unicorn", "Iris", "brand director", kits_dir=kits)
    _stub_backend(monkeypatch)
    agent_identity.generate_avatar("magic-unicorn", "iris", kits_dir=kits)


def test_avatar_size_derivative_returns_square_png(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient, api_kits: Path
) -> None:
    _seed_avatar(monkeypatch, api_kits)

    resp = api_client.get("/api/brands/magic-unicorn/agents/iris/avatar?size=64")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(resp.content))
    assert img.format == "PNG"
    assert img.size == (64, 64)


def test_avatar_unsupported_size_is_422(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient, api_kits: Path
) -> None:
    _seed_avatar(monkeypatch, api_kits)
    resp = api_client.get("/api/brands/magic-unicorn/agents/iris/avatar?size=99")
    assert resp.status_code == 422


def test_avatar_no_size_serves_full_image(
    monkeypatch: pytest.MonkeyPatch, api_client: TestClient, api_kits: Path
) -> None:
    # No size -> the full source avatar (the stub PNG is 64x64), still 200 image/png.
    _seed_avatar(monkeypatch, api_kits)
    resp = api_client.get("/api/brands/magic-unicorn/agents/iris/avatar")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
