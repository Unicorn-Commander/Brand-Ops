"""Workspace-first brand-kit resolution with shared suite fallbacks."""
from __future__ import annotations

import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

# Import the API side first so the MCP module's import cycle is initialized in
# the same order as the rest of the MCP test suite.
import brand_ops.api.app  # noqa: F401
from brand_ops import agent_identity, brand_loader, mcp_server
from brand_ops.backends.base import GenRequest, GenResult

_REPO = Path(__file__).resolve().parents[1]
_WS_A = "workspace-a"
_WS_B = "workspace-b"


@pytest.fixture(autouse=True)
def _reset_scope() -> Iterator[None]:
    brand_loader.reset_workspace_scope()
    try:
        yield
    finally:
        brand_loader.reset_workspace_scope()


@pytest.fixture
def kits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "brand-kits"
    # Keep the fixture close to the deploy shape: a shared built-in at the flat
    # root, with workspace-created kits added below it by new_brand.
    (root / "magic-unicorn").mkdir(parents=True)
    shutil.copy(
        _REPO / "brand-kits" / "magic-unicorn" / "brand.yaml",
        root / "magic-unicorn" / "brand.yaml",
    )
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)
    return root


def test_new_brand_agents_and_scopes_are_workspace_local(kits: Path) -> None:
    brand_loader.set_workspace_scope(_WS_A, frozenset())
    created = mcp_server.new_brand("rivergreen-coffee", "Rivergreen Coffee")

    assert created["slug"] == "rivergreen-coffee"
    assert created["scope"] == "workspace"
    assert (kits / _WS_A / "rivergreen-coffee" / "brand.yaml").is_file()

    agent = mcp_server.create_agent("rivergreen-coffee", "Moss", "coffee guide")
    assert agent["slug"] == "moss"
    assert (kits / _WS_A / "rivergreen-coffee" / "agents" / "moss" / "agent.json").is_file()
    assert mcp_server.list_brand_agents("rivergreen-coffee")["count"] == 1

    listed = mcp_server.list_brands()
    by_slug = {entry["slug"]: entry for entry in listed["brands"]}
    assert by_slug["rivergreen-coffee"]["scope"] == "workspace"
    assert by_slug["magic-unicorn"]["scope"] == "shared"

    brand_loader.reset_workspace_scope()
    brand_loader.set_workspace_scope(_WS_B, frozenset())
    assert "error" in mcp_server.get_brand("rivergreen-coffee")
    assert "error" in mcp_server.list_brand_agents("rivergreen-coffee")
    assert "error" not in mcp_server.get_brand("magic-unicorn")


def test_avatar_writes_workspace_kit_and_not_shared(
    monkeypatch: pytest.MonkeyPatch, kits: Path
) -> None:
    image = io.BytesIO()
    Image.new("RGBA", (64, 64), (182, 110, 255, 255)).save(image, format="PNG")
    png = image.getvalue()

    class _FakeBackend:
        def generate(self, req: GenRequest) -> GenResult:
            return GenResult(images=[png], mime="image/png", backend="fake", model="fake")

    monkeypatch.setattr(
        agent_identity,
        "resolve_image_backend",
        lambda default, requested, settings: ("fake", _FakeBackend(), None),
    )
    monkeypatch.setattr(agent_identity.cost, "enforce_image_quota_sync", lambda ws: None)
    monkeypatch.setattr(agent_identity.cost, "meter_image_spend_sync", lambda *args, **kwargs: None)

    brand_loader.set_workspace_scope(_WS_A, frozenset())
    mcp_server.new_brand("rivergreen-coffee", "Rivergreen Coffee")
    mcp_server.create_agent("rivergreen-coffee", "Moss", "coffee guide")
    result = mcp_server.generate_agent_avatar("rivergreen-coffee", "moss")

    assert "error" not in result
    assert (kits / _WS_A / "rivergreen-coffee" / "agents" / "moss" / "avatar.png").is_file()
    assert not (kits / "rivergreen-coffee").exists()

    brand_loader.reset_workspace_scope()
    brand_loader.set_workspace_scope(_WS_B, frozenset())
    assert "error" in mcp_server.generate_agent_avatar("rivergreen-coffee", "moss")
