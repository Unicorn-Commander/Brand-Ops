"""Self-serve signup tests: name/slug derivation, the provisioning service, the routes.

No Postgres needed. The pure helpers (slugify, suggested_name, the feature flag)
are tested directly; the provisioning service is tested with the DB layer stubbed
(in-memory fakes that record their calls), exactly like the admin route tests; and
the two routes are tested with ``require_principal`` overridden to inject an authed
or anonymous principal, so the gate (flag off, anonymous, no DB) and the happy path
are covered without any token machinery or database.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import InvalidRequestError

from brand_ops.api import onboarding as ob
from brand_ops.api.auth import Principal, require_principal
from brand_ops.api.onboarding import (
    ProvisionResult,
    onboarding_router,
    self_serve_enabled,
    slugify,
    suggested_name,
)

_UID = "user-1"
_WS = "019e50a3-d995-723f-ab66-0f765f92c0f4"


# ---------------------------------------------------------------------------
# Pure helpers (no DB)
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Acme Co!") == "acme-co"
    assert slugify("  Multiple   Spaces ") == "multiple-spaces"
    assert slugify("Jane's Coffee & Tea") == "jane-s-coffee-tea"


def test_slugify_trims_and_collapses() -> None:
    assert slugify("---Edges---") == "edges"
    assert slugify("a___b") == "a-b"


def test_slugify_empty_when_nothing_usable() -> None:
    assert slugify("") == ""
    assert slugify(None) == ""
    assert slugify("###") == ""
    assert slugify("   ") == ""


def test_slugify_caps_length() -> None:
    out = slugify("x" * 200)
    assert len(out) <= 60
    assert out == "x" * 60


def test_suggested_name_prefers_name_then_username_then_email() -> None:
    assert suggested_name({"name": "Jane Doe"}, _UID) == "Jane Doe"
    assert suggested_name({"preferred_username": "jdoe"}, _UID) == "jdoe"
    assert suggested_name({"email": "jane@example.com"}, _UID) == "jane"


def test_suggested_name_falls_back_to_subject() -> None:
    # No usable claims: a short, stable label off the subject, never empty.
    assert suggested_name({}, "abcdef123456") == "workspace-abcdef12"
    assert suggested_name(None, "") == "My workspace"


def test_self_serve_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_OPS_SELF_SERVE_SIGNUP", raising=False)
    assert self_serve_enabled() is False
    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", truthy)
        assert self_serve_enabled() is True
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "off")
    assert self_serve_enabled() is False


# ---------------------------------------------------------------------------
# Provisioning service (DB layer stubbed)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Stub the DB layer so the provisioning service runs with no Postgres.

    No-op async context managers stand in for the sessionmaker + scope helper, and
    the repository calls are recorded in-memory. ``existing`` lets a test control
    what ``resolve_context`` reports (None = no workspace yet; a tuple = the caller
    already has one). ``taken`` is the set of slugs ``workspace_slug_exists`` should
    report as already used, so the uniqueness suffix can be exercised.
    """
    calls: dict = {
        "upsert_ws": [],
        "upsert_member": [],
        "audit": [],
        "existing": None,
        "taken": set(),
    }

    class _Txn:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *a):
            # Commit resets the per-transaction query flag: a fresh begin() is fine.
            self._session._queried = False
            self._session._in_txn = False
            return False

    class _FakeSession:
        def __init__(self):
            self._queried = False
            self._in_txn = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def begin(self):
            # Model SQLAlchemy autobegin: a query issued before an explicit begin()
            # autobegins a transaction, so a later begin() must raise. This is the
            # exact failure the live synthetic E2E caught (a SELECT before begin()).
            if self._queried and not self._in_txn:
                raise InvalidRequestError("A transaction is already begun on this Session.")
            self._in_txn = True
            return _Txn(self)

    def _fake_sessionmaker():
        return _FakeSession

    class _NullScope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    def _fake_scoped_session(session, **kw):
        return _NullScope()

    from brand_ops.db.tenancy import WorkspaceContext

    async def _fake_resolve(uc_uid, *, requested_workspace_id=None):
        e = calls["existing"]
        if e is None:
            return WorkspaceContext()
        return WorkspaceContext(workspace_id=e[0], workspace_slug=e[1], role=e[2])

    async def _fake_slug_exists(session, slug):
        session._queried = True  # a SELECT autobegins a transaction
        return slug in calls["taken"]

    async def _fake_upsert_workspace(session, *, workspace_id, slug):
        session._queried = True
        calls["upsert_ws"].append((workspace_id, slug))
        return None

    async def _fake_upsert_membership(session, *, user_key, workspace_id, role, is_default):
        calls["upsert_member"].append((user_key, workspace_id, role, is_default))
        return None

    def _fake_audit(actor, action, ws_id, summary=None, result="ok"):
        calls["audit"].append((actor, action, ws_id, summary))
        return {}

    monkeypatch.setattr(ob, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(ob, "scoped_session", _fake_scoped_session)
    monkeypatch.setattr(ob, "resolve_context", _fake_resolve)
    monkeypatch.setattr(ob.repo, "workspace_slug_exists", _fake_slug_exists)
    monkeypatch.setattr(ob.repo, "upsert_workspace", _fake_upsert_workspace)
    monkeypatch.setattr(ob.repo, "upsert_membership", _fake_upsert_membership)
    monkeypatch.setattr(ob.admin_audit, "append_admin_event", _fake_audit)
    yield calls


@pytest.mark.asyncio
async def test_provision_creates_owner_default(stub_db: dict) -> None:
    result = await ob.provision_personal_workspace(
        _UID, display_name="Acme Co", claims={"name": "ignored when display set"}
    )
    assert result.created is True
    assert result.role == "owner"
    assert result.slug == "acme-co"
    # A workspace anchor was created with a freshly minted uuid, then the caller was
    # granted an owner, default membership in it.
    assert len(stub_db["upsert_ws"]) == 1
    ws_id, slug = stub_db["upsert_ws"][0]
    assert slug == "acme-co"
    assert result.workspace_id == ws_id
    assert stub_db["upsert_member"] == [(_UID, ws_id, "owner", True)]
    # The provision was recorded to the durable audit log.
    assert stub_db["audit"] and stub_db["audit"][0][1] == "self_provision_workspace"


@pytest.mark.asyncio
async def test_provision_derives_name_from_claims(stub_db: dict) -> None:
    # No display_name: the slug comes from the caller's identity claims.
    result = await ob.provision_personal_workspace(_UID, claims={"name": "Jane Doe"})
    assert result.slug == "jane-doe"


@pytest.mark.asyncio
async def test_provision_is_idempotent_when_already_a_member(stub_db: dict) -> None:
    # The caller already has a workspace: it is returned, nothing is created.
    stub_db["existing"] = (_WS, "existing-ws", "owner")
    result = await ob.provision_personal_workspace(_UID, display_name="New One")
    assert result.created is False
    assert result.workspace_id == _WS
    assert result.slug == "existing-ws"
    assert stub_db["upsert_ws"] == []
    assert stub_db["upsert_member"] == []
    assert stub_db["audit"] == []


@pytest.mark.asyncio
async def test_provision_disambiguates_taken_slug(stub_db: dict) -> None:
    # "acme" is taken; the next free numeric variant is chosen.
    stub_db["taken"] = {"acme"}
    result = await ob.provision_personal_workspace(_UID, display_name="Acme")
    assert result.slug == "acme-2"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _client(monkeypatch: pytest.MonkeyPatch, principal: Principal) -> TestClient:
    """A TestClient mounting only the onboarding router, authed as ``principal``."""
    app = FastAPI()
    app.include_router(onboarding_router)
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def test_status_enabled_no_workspace_needs_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    principal = Principal(uc_uid=_UID, issuer="keycloak", claims={"name": "Jane"})
    client = _client(monkeypatch, principal)
    body = client.get("/api/onboarding").json()
    assert body == {
        "enabled": True,
        "needs_onboarding": True,
        "has_workspace": False,
        "suggested_name": "Jane",
    }


def test_status_disabled_never_needs_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_OPS_SELF_SERVE_SIGNUP", raising=False)
    client = _client(monkeypatch, Principal(uc_uid=_UID, issuer="keycloak"))
    body = client.get("/api/onboarding").json()
    assert body["enabled"] is False
    assert body["needs_onboarding"] is False


def test_status_has_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    client = _client(
        monkeypatch,
        Principal(uc_uid=_UID, issuer="keycloak", workspace_id=_WS, role="owner"),
    )
    body = client.get("/api/onboarding").json()
    assert body["has_workspace"] is True
    assert body["needs_onboarding"] is False


def test_status_anonymous_never_needs_onboarding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    client = _client(monkeypatch, Principal(uc_uid="anonymous", anonymous=True))
    body = client.get("/api/onboarding").json()
    assert body["needs_onboarding"] is False
    assert body["has_workspace"] is False


def test_provision_route_disabled_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_OPS_SELF_SERVE_SIGNUP", raising=False)
    client = _client(monkeypatch, Principal(uc_uid=_UID, issuer="keycloak"))
    resp = client.post("/api/onboarding/provision", json={})
    assert resp.status_code == 403


def test_provision_route_anonymous_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    client = _client(monkeypatch, Principal(uc_uid="anonymous", anonymous=True))
    resp = client.post("/api/onboarding/provision", json={})
    assert resp.status_code == 403


def test_provision_route_no_db_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _client(monkeypatch, Principal(uc_uid=_UID, issuer="keycloak"))
    resp = client.post("/api/onboarding/provision", json={"display_name": "Acme"})
    assert resp.status_code == 503


def test_provision_route_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_SELF_SERVE_SIGNUP", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")

    async def _fake_provision(uc_uid, *, display_name=None, claims=None):
        assert uc_uid == _UID  # always the authed caller, never a body value
        return ProvisionResult(workspace_id=_WS, slug="acme", role="owner", created=True)

    monkeypatch.setattr(ob, "provision_personal_workspace", _fake_provision)
    client = _client(monkeypatch, Principal(uc_uid=_UID, issuer="keycloak"))
    resp = client.post("/api/onboarding/provision", json={"display_name": "Acme"})
    assert resp.status_code == 200
    assert resp.json() == {
        "workspace_id": _WS,
        "slug": "acme",
        "role": "owner",
        "is_default": True,
        "created": True,
    }
