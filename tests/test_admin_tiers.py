"""Admin capability-tier tests: the READ < WRITE < DESTROY gate over the admin API.

These cover the tiering layer added on top of the existing admin allow-list
(``test_admin.py`` covers the base gate). All of it runs in the no-database
tier: the tier check is a FastAPI dependency that resolves before any route body
(and so before ``_require_db``), so a denial is observable without a live
Postgres, and a passing destroy-tier admin is proven by the route reaching its
DB requirement (a 503 when ``DATABASE_URL`` is unset) rather than a 403.

The fixture pattern mirrors ``test_admin.py``: ``require_principal`` is overridden
to return an allow-listed principal and ``BRAND_OPS_ADMIN_SUBS`` is set to match,
with ``BRAND_OPS_ADMIN_TIERS`` layered on to assign tiers. No token machinery and
no Keycloak.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from brand_ops import admin_audit
from brand_ops.api.admin import (
    admin_router,
    admin_subs,
    admin_tier,
    require_admin,
)
from brand_ops.api.auth import Principal, require_principal

_ADMIN_UID = "admin-1"
_WS = "019e50a3-d995-723f-ab66-0f765f92c0f4"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_admin_audit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every admin-audit write inside the test's tmp tree.

    A tier-denied request still runs ``require_admin`` (which audits nothing on
    the allow path) and the tier gate, and a passing request that reaches the
    route may append to the durable admin-audit log, which resolves its own
    DEFAULT_KITS_DIR at call time. Redirect it to a throwaway root so no admin
    test leaks an audit line into the repo's real brand-kits tree.
    """
    monkeypatch.setattr(admin_audit, "DEFAULT_KITS_DIR", tmp_path / "default-audit-root")


def _admin_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    uc_uid: str = _ADMIN_UID,
    tiers: str | None = None,
) -> TestClient:
    """A TestClient mounting the admin router, authed as an allow-listed admin.

    ``BRAND_OPS_ADMIN_SUBS`` is set to ``uc_uid`` and ``require_principal`` is
    overridden to return that (non-anonymous) principal, so ``require_admin``
    admits the caller. ``tiers`` (if given) is written to ``BRAND_OPS_ADMIN_TIERS``
    so the tier gate sees the configured assignment; left unset, the admin keeps
    the default ``destroy`` tier.
    """
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", uc_uid)
    if tiers is None:
        monkeypatch.delenv("BRAND_OPS_ADMIN_TIERS", raising=False)
    else:
        monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", tiers)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid=uc_uid, issuer="keycloak"
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# admin_tier resolution (no DB, no HTTP)
# ---------------------------------------------------------------------------


def test_admin_tier_returns_configured_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "alice,bob,carol")
    monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", "alice:read,bob:write,carol:destroy")
    assert admin_tier("alice") == "read"
    assert admin_tier("bob") == "write"
    assert admin_tier("carol") == "destroy"


def test_admin_tier_defaults_untiered_admin_to_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An admin not named in BRAND_OPS_ADMIN_TIERS keeps full power (backward compat).
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "alice,bob")
    monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", "alice:read")
    assert admin_tier("bob") == "destroy"


def test_admin_tier_defaults_to_destroy_when_tiers_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "alice")
    monkeypatch.delenv("BRAND_OPS_ADMIN_TIERS", raising=False)
    assert admin_tier("alice") == "destroy"


def test_admin_tier_invalid_tier_falls_back_to_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unknown/misspelled tier string is treated as the least privilege.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "alice")
    monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", "alice:superuser")
    assert admin_tier("alice") == "read"


def test_admin_tier_is_read_when_env_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sanity: the parser trims and lowercases, and read is a real (not fallback) tier.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "alice")
    monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", " alice : READ ")
    assert admin_tier("alice") == "read"
    # admin_subs is unaffected by the tiers var.
    assert admin_subs() == frozenset({"alice"})


# ---------------------------------------------------------------------------
# The tier gate over HTTP routes (no DB)
# ---------------------------------------------------------------------------


def test_read_tier_admin_denied_on_write_route(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read-tier admin must not be able to create a workspace (a WRITE route).
    # The tier dependency 403s before the route body, so no DB is needed.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:read")
    resp = client.post("/api/admin/workspaces", json={"workspace_id": _WS, "slug": "x"})
    assert resp.status_code == 403


def test_read_tier_admin_denied_on_destroy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A read-tier admin must not be able to delete a workspace (a DESTROY route).
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:read")
    resp = client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    )
    assert resp.status_code == 403


def test_read_tier_admin_allowed_on_read_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same read-tier admin passes the gate on a READ route (audit verify).
    # That route needs no DB, so a passing gate yields a real 200.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:read")
    resp = client.get(f"/api/admin/workspaces/{_WS}/audit/verify")
    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == _WS


def test_read_tier_admin_passes_gate_on_usage_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # /usage is a READ route but needs a DB; with DATABASE_URL unset a read-tier
    # admin clears the tier gate and reaches the DB requirement (503), proving the
    # gate did NOT block the read.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:read")
    resp = client.get("/api/admin/usage")
    assert resp.status_code != 403
    assert resp.status_code == 503  # blocked by _require_db, not by the tier gate


def test_write_tier_admin_passes_write_gate_but_denied_on_destroy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A write-tier admin clears a WRITE route's gate (reaching the DB requirement)
    # but is still 403 on a DESTROY route.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:write")
    create = client.post("/api/admin/workspaces", json={"workspace_id": _WS, "slug": "x"})
    assert create.status_code != 403
    assert create.status_code == 503  # passed the tier gate, then hit _require_db
    delete = client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    )
    assert delete.status_code == 403


def test_destroy_tier_admin_passes_destroy_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A destroy-tier admin clears the DESTROY route's gate. With no DATABASE_URL it
    # then 503s on _require_db, which is fine: the point is it is NOT a tier 403.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=f"{_ADMIN_UID}:destroy")
    resp = client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    )
    assert resp.status_code != 403
    assert resp.status_code == 503


def test_untiered_admin_passes_destroy_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Backward compat: an admin with NO tier assignment defaults to destroy and so
    # still passes the most privileged gate, exactly as before tiering existed.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch, tiers=None)
    resp = client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    )
    assert resp.status_code != 403
    assert resp.status_code == 503


def test_non_admin_still_403_from_base_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tiering must not weaken the base gate: a caller not in BRAND_OPS_ADMIN_SUBS is
    # still 403 from require_admin, regardless of any tier assignment they appear in.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1")
    monkeypatch.setenv("BRAND_OPS_ADMIN_TIERS", "intruder:destroy")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid="intruder", issuer="keycloak"
    )
    client = TestClient(app)
    # Every tier of route is 403 for a non-admin (the base gate fires first).
    assert client.get(f"/api/admin/workspaces/{_WS}/audit/verify").status_code == 403
    assert client.post(
        "/api/admin/workspaces", json={"workspace_id": _WS, "slug": "x"}
    ).status_code == 403
    assert client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    ).status_code == 403


def test_require_admin_unchanged_for_listed_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The base require_admin export still works on its own (other code depends on
    # it); tiering is additive, not a replacement.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1,admin-2")
    p = Principal(uc_uid="admin-2", issuer="keycloak")
    assert require_admin(p) is p
    with pytest.raises(HTTPException) as ei:
        require_admin(Principal(uc_uid="nope", issuer="keycloak"))
    assert ei.value.status_code == 403
