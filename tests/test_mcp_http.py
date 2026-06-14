"""The opt-in HTTP MCP transport: discovery manifest, the bearer gate, and that
enabling it does not disturb the core app.

The FastMCP StreamableHTTP session manager can only be started once per process,
so the lifespan-bearing assertions all live in a single TestClient context.
"""
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app


def test_mcp_http_surface(monkeypatch):
    monkeypatch.setenv("MCP_HTTP_ENABLED", "true")
    monkeypatch.setenv("MCP_AUTH_REQUIRED", "true")
    app = create_app()
    with TestClient(app) as client:
        # The discovery manifest is public and lists the tools.
        r = client.get("/.well-known/mcps.json")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "brand-ops"
        assert body["transport"] == "streamable-http"
        assert body["auth"]["required"] is True
        assert body["entitlement"] == "brand_ops"
        assert any(t["name"] == "list_brands" for t in body["tools"])
        assert len(body["tools"]) >= 10

        # The protocol endpoint is gated: no bearer -> 401, before the MCP app.
        gated = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
        assert gated.status_code == 401

        # The core REST app is unaffected by mounting the MCP transport.
        assert client.get("/health").status_code == 200


def test_mcp_auth_required_defaults_on(monkeypatch):
    from brand_ops.api.mcp_http import mcp_auth_required

    monkeypatch.setenv("MCP_AUTH_REQUIRED", "false")
    assert mcp_auth_required() is False
    monkeypatch.delenv("MCP_AUTH_REQUIRED", raising=False)
    assert mcp_auth_required() is True


def test_mcp_http_disabled_by_default(monkeypatch):
    """With the env unset the MCP routes are absent and the app has no lifespan."""
    monkeypatch.delenv("MCP_HTTP_ENABLED", raising=False)
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/.well-known/mcps.json").status_code == 404
        assert client.get("/health").status_code == 200


def test_mcp_server_is_stateless_for_tenant_scope():
    """The HTTP MCP MUST be stateless so a tool inherits the per-request workspace
    scope.

    In stateful mode FastMCP runs each tool on a task spawned at session
    `initialize`, before mcp_http sets the scope, and a ContextVar only snapshots
    at task creation -- so the tool would run UNSCOPED (every tenant's brands).
    Stateless mode starts the server task from the per-request call, after the
    scope is set. This pins the fix; flipping it back to stateful re-opens a
    critical cross-tenant read on mcp.brand-ops.*.
    """
    from brand_ops.mcp_server import mcp

    assert mcp.settings.stateless_http is True
