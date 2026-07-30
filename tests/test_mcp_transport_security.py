"""The HTTP MCP surface has to be reachable under its real hostname.

``FastMCP()`` defaults ``host="127.0.0.1"``, and that default AUTO-ENABLES DNS
rebinding protection with ``allowed_hosts`` limited to the loopback names. Behind
Traefik the Host header is ``mcp.brand-ops.magicunicorn.dev``, which matched
nothing, so every production request died at ``421 Misdirected Request`` inside
the MCP transport -- AFTER the auth wrapper had already accepted the credential.

It failed closed, so it was never a hole. What it was is proof that the HTTP
surface had never been exercised past the auth wrapper at all.

The fix configures the allow-list from configuration. Protection stays ON and is
never wildcarded; these tests hold both halves of that, using the SDK's real
middleware to produce the real verdict rather than re-implementing its matching.
"""
from __future__ import annotations

import pytest
from mcp.server.transport_security import TransportSecurityMiddleware

from brand_ops.api import mcp_http

PROD_HOST = "mcp.brand-ops.magicunicorn.dev"


def test_protection_stays_on() -> None:
    """Configuring the hostname must not be a way of switching the check off."""
    assert mcp_http._transport_security().enable_dns_rebinding_protection is True


def test_the_production_hostname_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug, directly: the deployed Host header used to match nothing."""
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    settings = mcp_http._transport_security()
    mw = TransportSecurityMiddleware(settings)
    assert mw._validate_host(PROD_HOST) is True
    # ...and the same name on an explicit port, which is what an internal probe
    # or a non-443 router sends.
    assert mw._validate_host(f"{PROD_HOST}:8443") is True


def test_a_foreign_hostname_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The protection is still doing its job: this is the rebinding case."""
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    mw = TransportSecurityMiddleware(mcp_http._transport_security())
    for hostile in ("evil.example.com", "attacker.test:80", "", None):
        assert mw._validate_host(hostile) is False


def test_loopback_keeps_working(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adding the real hostname must not take the local path away."""
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    mw = TransportSecurityMiddleware(mcp_http._transport_security())
    for local in ("127.0.0.1", "127.0.0.1:8080", "localhost", "localhost:9000"):
        assert mw._validate_host(local) is True


def test_the_hostname_comes_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """A node on another name configures it; nothing is hard-coded for it."""
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://mcp.brand.example.dev/mcp")
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    mw = TransportSecurityMiddleware(mcp_http._transport_security())
    assert mw._validate_host("mcp.brand.example.dev") is True
    assert mw._validate_host(PROD_HOST) is False


def test_extra_router_names_can_be_added(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "brand-ops.internal, mcp.alt.example ")
    mw = TransportSecurityMiddleware(mcp_http._transport_security())
    assert mw._validate_host("brand-ops.internal") is True
    assert mw._validate_host("mcp.alt.example") is True
    assert mw._validate_host("mcp.other.example") is False


def test_a_wildcard_entry_cannot_disable_the_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Allow everything" configuration is dropped, not honoured.

    A wildcard host allow-list IS the rebinding hole. A typo or a copy-pasted
    ``*`` must not quietly re-open what this setting exists to close.
    """
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "*, *.example.com, evil*")
    settings = mcp_http._transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert not any(
        entry == "*" or (entry.count("*") and not entry.endswith(":*"))
        for entry in settings.allowed_hosts
    ), settings.allowed_hosts
    mw = TransportSecurityMiddleware(settings)
    assert mw._validate_host("anything.example.com") is False
    assert mw._validate_host("evil.test") is False
    # The legitimate names configured elsewhere still work.
    assert mw._validate_host(PROD_HOST) is True


def test_origins_are_allow_listed_for_the_same_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MCP_PUBLIC_URL", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    mw = TransportSecurityMiddleware(mcp_http._transport_security())
    assert mw._validate_origin(f"https://{PROD_HOST}") is True
    assert mw._validate_origin("https://evil.example.com") is False
    # A public hostname is https only: no http origin for it.
    assert mw._validate_origin(f"http://{PROD_HOST}") is False


def test_the_settings_are_actually_installed_on_the_served_app() -> None:
    """Not merely computed: ``_ensure_inner`` must publish them BEFORE building.

    ``streamable_http_app()`` reads ``settings.transport_security`` when it lazily
    builds the session manager, so setting it afterwards would compute a correct
    allow-list that the server never sees.
    """
    mcp_http._ensure_inner()
    from brand_ops.mcp_server import mcp

    installed = mcp.settings.transport_security
    assert installed is not None
    assert installed.enable_dns_rebinding_protection is True
    assert PROD_HOST in installed.allowed_hosts
