"""The auth -> resolver -> ContextVar wiring for the entitlement gate.

test_entitlement.py pins the gate's decision matrix and test_entitlements_client.py
pins the resolver; this file pins the SEAM between them in
``api.auth._scope_principal``: the resolved feature_keys are published into the
request-scoped ContextVar the gate reads, only for a forwardable Keycloak token,
and a resolver failure can never 500 the request (it degrades to UNKNOWN).

The tenancy resolver (``resolve_context``) is stubbed so this needs no database.
"""
from __future__ import annotations

import types

import pytest

from brand_ops import entitlement
from brand_ops.api import auth
from brand_ops.api.auth import Principal, _scope_principal


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    entitlement.reset_entitlement_scope()

    async def _fake_ctx(uc_uid, requested_workspace_id=None):
        return types.SimpleNamespace(
            workspace_id="ws-1",
            workspace_slug="ws",
            role="owner",
            allowed_brand_slugs=frozenset(),
        )

    monkeypatch.setattr(auth, "resolve_context", _fake_ctx)
    yield
    entitlement.reset_entitlement_scope()


@pytest.mark.asyncio
async def test_keycloak_token_publishes_resolved_grants(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Keycloak-issued caller: the raw bearer is forwarded under the caller's
    # uc_uid cache key, and the resolved grant list lands in the gate's ContextVar.
    captured: dict = {}

    async def _resolve(token, *, cache_key, org_id=None):
        captured["token"] = token
        captured["cache_key"] = cache_key
        captured["org_id"] = org_id
        return ["meeting_ops_access", "brand_ops_access"]

    monkeypatch.setattr(auth, "resolve_entitlements", _resolve)

    await _scope_principal(Principal(uc_uid="user-1", issuer="keycloak"), None, "raw-token")

    assert entitlement.get_current_entitlement() == ["meeting_ops_access", "brand_ops_access"]
    assert captured == {"token": "raw-token", "cache_key": "user-1", "org_id": None}


@pytest.mark.asyncio
async def test_brigade_token_is_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Brigade token is a different issuer the uchub endpoint would reject, so it
    # is never sent; the gate signal stays UNKNOWN (None).
    called = {"n": 0}

    async def _resolve(*args, **kwargs):
        called["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(auth, "resolve_entitlements", _resolve)

    await _scope_principal(Principal(uc_uid="agent-1", issuer="brigade"), None, "brigade-token")

    assert called["n"] == 0
    assert entitlement.get_current_entitlement() is None


@pytest.mark.asyncio
async def test_missing_token_is_not_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keycloak issuer but no raw token in hand -> nothing to forward -> UNKNOWN.
    called = {"n": 0}

    async def _resolve(*args, **kwargs):
        called["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(auth, "resolve_entitlements", _resolve)

    await _scope_principal(Principal(uc_uid="user-1", issuer="keycloak"), None, None)

    assert called["n"] == 0
    assert entitlement.get_current_entitlement() is None


@pytest.mark.asyncio
async def test_resolver_exception_degrades_to_unknown_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The resolver is contracted never to raise; if a bug ever makes it, the auth
    # path must swallow it and treat the caller as UNKNOWN, never 500 the request.
    async def _boom(*args, **kwargs):
        raise RuntimeError("resolver contract broken")

    monkeypatch.setattr(auth, "resolve_entitlements", _boom)

    # Must NOT raise.
    await _scope_principal(Principal(uc_uid="user-1", issuer="keycloak"), None, "raw-token")
    assert entitlement.get_current_entitlement() is None
