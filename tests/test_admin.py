"""Admin API tests: the authz gate, offboarding, export, provisioning, usage.

Two tiers, mirroring the rest of the suite:

* The no-database tier (always runs) covers everything that does not need a live
  Postgres: the admin allow-list gate (allow and deny, including deny-by-default
  when unset and the anonymous principal), the on-disk purge and the zip export
  against a temp brand-kits tree, the path-traversal guard, the delete confirm
  gate, and the route wiring (create/grant/revoke/delete/usage) with the
  repository functions stubbed so the HTTP contract and idempotency are exercised
  without a database.
* The Postgres tier (skipped unless ``BRAND_OPS_RUN_DB_TESTS=1`` and
  ``DATABASE_URL`` name the ``brand_ops_app`` NOBYPASSRLS role on a migrated DB,
  exactly like ``test_db_tenancy.py``) proves the real DELETE CASCADE removes a
  tenant's brands/assets/memberships/spend, the create -> grant -> revoke
  lifecycle, and the cross-tenant usage rollup under RLS.

An admin caller is injected by overriding ``require_principal`` (which
``require_admin`` depends on) to return an allow-listed principal, with
``BRAND_OPS_ADMIN_SUBS`` set to match; this never touches Keycloak.
"""
from __future__ import annotations

import os
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brand_ops import admin_ops
from brand_ops.api import admin as admin_mod
from brand_ops.api.admin import admin_router, admin_subs, require_admin
from brand_ops.api.auth import Principal, require_principal

_ADMIN_UID = "admin-1"
_WS = "019e50a3-d995-723f-ab66-0f765f92c0f4"
_WS2 = "019e5222-0000-7000-a000-000000000002"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def kits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A temp brand-kits root the admin on-disk helpers resolve against.

    ``admin_ops`` binds ``DEFAULT_KITS_DIR`` at import, and ``_kits_root()`` reads
    it from the module namespace at call time, so patching the attribute on the
    ``admin_ops`` module redirects every purge/export to this throwaway tree.
    """
    root = tmp_path / "brand-kits"
    root.mkdir()
    monkeypatch.setattr(admin_ops, "DEFAULT_KITS_DIR", root)
    return root


def _write_workspace_tree(root: Path, ws_id: str, slug: str) -> Path:
    """Lay down brand-kits/<ws_id>/<slug>/ with a brand.yaml and two asset files."""
    ws_dir = root / ws_id / slug
    (ws_dir / "generated" / "og_image").mkdir(parents=True)
    (ws_dir / "brand.yaml").write_text(
        f"brand:\n  slug: {slug}\n  display_name: {slug.title()}\n"
        "visual:\n  colors:\n    primary: '#112233'\n    accent: '#445566'\n"
    )
    (ws_dir / "generated" / "og_image" / "card.png").write_bytes(b"png-bytes")
    (ws_dir / "generated" / "og_image" / "card.png.json").write_text('{"asset_type":"og_image"}')
    return ws_dir


def _admin_client(monkeypatch: pytest.MonkeyPatch, *, uc_uid: str = _ADMIN_UID) -> TestClient:
    """A TestClient mounting only the admin router, authed as an allow-listed admin.

    ``BRAND_OPS_ADMIN_SUBS`` is set to the caller's uc_uid and ``require_principal``
    is overridden to return that (non-anonymous) principal, so ``require_admin``
    admits the request without any token machinery.
    """
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", uc_uid)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid=uc_uid, issuer="keycloak"
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# The admin gate (no DB)
# ---------------------------------------------------------------------------


def test_admin_subs_parses_and_trims(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", " a , b ,, c ")
    assert admin_subs() == frozenset({"a", "b", "c"})


def test_admin_subs_empty_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_OPS_ADMIN_SUBS", raising=False)
    assert admin_subs() == frozenset()


def test_require_admin_allows_listed_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1,admin-2")
    p = Principal(uc_uid="admin-2", issuer="keycloak")
    assert require_admin(p) is p


def test_require_admin_denies_non_listed_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        require_admin(Principal(uc_uid="intruder", issuer="keycloak"))
    assert ei.value.status_code == 403


def test_require_admin_denies_anonymous_even_if_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Defense in depth: even if "anonymous" were put in the list, the anonymous
    # principal is never an admin.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "anonymous")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        require_admin(Principal(uc_uid="anonymous", anonymous=True))
    assert ei.value.status_code == 403


def test_require_admin_deny_by_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAND_OPS_ADMIN_SUBS", raising=False)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        require_admin(Principal(uc_uid="anyone", issuer="keycloak"))
    assert ei.value.status_code == 403


def test_non_admin_gets_403_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    # An authed but non-listed caller is 403 on a real route.
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid="not-admin", issuer="keycloak"
    )
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 403
    assert client.post(
        "/api/admin/workspaces", json={"workspace_id": _WS, "slug": "x"}
    ).status_code == 403
    assert client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    ).status_code == 403


def test_anonymous_denied_when_auth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real require_principal (auth off) yields anonymous, which the gate must
    # reject even with an empty allow-list (deny-by-default).
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("BRAND_OPS_ADMIN_SUBS", raising=False)
    app = FastAPI()
    app.include_router(admin_router)
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 403


# ---------------------------------------------------------------------------
# On-disk purge + export (no DB)
# ---------------------------------------------------------------------------


def test_purge_removes_subtree_and_reports_summary(kits: Path) -> None:
    _write_workspace_tree(kits, _WS, "acme")
    assert (kits / _WS).is_dir()
    res = admin_ops.purge_workspace_tree(_WS)
    assert res["removed"] is True
    assert res["brands"] == ["acme"]
    assert res["file_count"] == 3
    assert not (kits / _WS).exists()


def test_purge_is_idempotent(kits: Path) -> None:
    # A workspace with no subtree at all: purge is a safe no-op.
    res = admin_ops.purge_workspace_tree(_WS)
    assert res["removed"] is False
    assert res["file_count"] == 0


def test_purge_only_touches_its_own_subtree(kits: Path) -> None:
    _write_workspace_tree(kits, _WS, "acme")
    _write_workspace_tree(kits, _WS2, "beta")
    admin_ops.purge_workspace_tree(_WS)
    assert not (kits / _WS).exists()
    # The other tenant's subtree is untouched.
    assert (kits / _WS2 / "beta" / "brand.yaml").exists()


def test_workspace_dir_rejects_traversal(kits: Path) -> None:
    # A non-uuid id (a path fragment) is refused before any join.
    with pytest.raises(ValueError):
        admin_ops._workspace_dir("../../etc")
    with pytest.raises(ValueError):
        admin_ops._workspace_dir("not-a-uuid")


def test_export_builds_zip_with_manifest_and_files(kits: Path) -> None:
    _write_workspace_tree(kits, _WS, "acme")
    zip_path = admin_ops.build_export_zip(_WS, slug="acme-ws")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "acme/brand.yaml" in names
            assert "acme/generated/og_image/card.png" in names
            import json

            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["workspace_id"] == _WS
        assert manifest["slug"] == "acme-ws"
        assert manifest["brands"] == ["acme"]
        assert manifest["brand_count"] == 1
        assert manifest["file_count"] == 3
        assert manifest["exported_at"] is None
    finally:
        zip_path.unlink(missing_ok=True)


def test_export_of_empty_workspace_still_valid_zip(kits: Path) -> None:
    # No subtree: a valid zip with just the manifest, never a 500.
    zip_path = admin_ops.build_export_zip(_WS)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.namelist() == ["manifest.json"]
    finally:
        zip_path.unlink(missing_ok=True)


def test_iter_and_cleanup_deletes_file(tmp_path: Path) -> None:
    f = tmp_path / "x.zip"
    f.write_bytes(b"hello-bytes")
    chunks = b"".join(admin_ops.iter_and_cleanup(f))
    assert chunks == b"hello-bytes"
    assert not f.exists()  # cleaned up after the stream


def test_export_route_streams_zip(monkeypatch: pytest.MonkeyPatch, kits: Path) -> None:
    # The export endpoint is fundamentally on-disk; with no DATABASE_URL the slug
    # lookup is skipped and the zip is built straight from the temp tree.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _write_workspace_tree(kits, _WS, "acme")
    client = _admin_client(monkeypatch)
    resp = client.get(f"/api/admin/workspaces/{_WS}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    import io

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "manifest.json" in zf.namelist()
        assert "acme/brand.yaml" in zf.namelist()


# ---------------------------------------------------------------------------
# Route wiring with the repository stubbed (no DB)
# ---------------------------------------------------------------------------


class _FakeWorkspace:
    def __init__(self, ws_id: str, slug: str) -> None:
        self.id = ws_id
        self.slug = slug


class _FakeMember:
    def __init__(self, ws_id: str, user_key: str, role: str, is_default: bool) -> None:
        self.workspace_id = ws_id
        self.user_key = user_key
        self.role = role
        self.is_default = is_default
        self.status = "active"


@pytest.fixture
def stub_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Stub the DB layer so admin routes run with no Postgres.

    Replaces the sessionmaker + scope helpers with no-op async context managers
    and the repository functions with in-memory fakes that record their calls,
    so the route contract (status, body shape, idempotency, the confirm gate) is
    tested without a database. DATABASE_URL is set so the routes' _require_db
    guard passes.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    calls: dict = {"upsert_ws": [], "upsert_member": [], "delete_member": [], "delete_ws": []}

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def begin(self):
            return _FakeSession()

    def _fake_sessionmaker():
        return _FakeSession

    class _NullScope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    def _fake_scoped_session(session, **kw):
        return _NullScope()

    async def _fake_upsert_workspace(session, *, workspace_id, slug):
        calls["upsert_ws"].append((workspace_id, slug))
        return _FakeWorkspace(workspace_id, slug)

    async def _fake_upsert_membership(session, *, user_key, workspace_id, role, is_default):
        calls["upsert_member"].append((user_key, workspace_id, role, is_default))
        return _FakeMember(workspace_id, user_key, role, is_default)

    async def _fake_delete_membership(session, *, workspace_id, user_key):
        calls["delete_member"].append((workspace_id, user_key))
        # First revoke of a given user removes a row; a repeat is a no-op.
        return (workspace_id, user_key) not in calls["delete_member"][:-1]

    async def _fake_delete_workspace(session, workspace_id):
        calls["delete_ws"].append(workspace_id)
        return True

    monkeypatch.setattr(admin_mod, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(admin_mod, "scoped_session", _fake_scoped_session)
    monkeypatch.setattr(admin_mod.repo, "upsert_workspace", _fake_upsert_workspace)
    monkeypatch.setattr(admin_mod.repo, "upsert_membership", _fake_upsert_membership)
    monkeypatch.setattr(admin_mod.repo, "delete_membership", _fake_delete_membership)
    monkeypatch.setattr(admin_mod.repo, "delete_workspace", _fake_delete_workspace)
    yield calls


def test_create_workspace_route(monkeypatch: pytest.MonkeyPatch, stub_db: dict) -> None:
    client = _admin_client(monkeypatch)
    resp = client.post("/api/admin/workspaces", json={"workspace_id": _WS, "slug": "acme"})
    assert resp.status_code == 200
    assert resp.json() == {"workspace_id": _WS, "slug": "acme"}
    assert stub_db["upsert_ws"] == [(_WS, "acme")]


def test_create_workspace_rejects_bad_id(monkeypatch: pytest.MonkeyPatch, stub_db: dict) -> None:
    client = _admin_client(monkeypatch)
    resp = client.post("/api/admin/workspaces", json={"workspace_id": "nope", "slug": "acme"})
    assert resp.status_code == 422
    assert stub_db["upsert_ws"] == []  # never reached the DB


def test_grant_member_route(monkeypatch: pytest.MonkeyPatch, stub_db: dict) -> None:
    client = _admin_client(monkeypatch)
    resp = client.post(
        f"/api/admin/workspaces/{_WS}/members",
        json={"uc_uid": "u-7", "role": "owner", "is_default": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["uc_uid"] == "u-7"
    assert body["role"] == "owner"
    assert body["is_default"] is True
    assert stub_db["upsert_member"] == [("u-7", _WS, "owner", True)]


def test_revoke_member_route_idempotent(monkeypatch: pytest.MonkeyPatch, stub_db: dict) -> None:
    client = _admin_client(monkeypatch)
    first = client.delete(f"/api/admin/workspaces/{_WS}/members/u-7")
    assert first.status_code == 200
    assert first.json()["revoked"] is True
    # A repeat revoke of the same member is a safe no-op (revoked=false).
    second = client.delete(f"/api/admin/workspaces/{_WS}/members/u-7")
    assert second.status_code == 200
    assert second.json()["revoked"] is False


def test_delete_workspace_requires_matching_confirm(
    monkeypatch: pytest.MonkeyPatch, stub_db: dict, kits: Path
) -> None:
    client = _admin_client(monkeypatch)
    # Wrong confirm id -> 422, and NOTHING is deleted (DB or disk).
    _write_workspace_tree(kits, _WS, "acme")
    resp = client.request(
        "DELETE",
        f"/api/admin/workspaces/{_WS}",
        json={"confirm_workspace_id": _WS2},
    )
    assert resp.status_code == 422
    assert stub_db["delete_ws"] == []
    assert (kits / _WS).exists()  # the subtree is untouched


def test_delete_workspace_route_deletes_db_and_disk(
    monkeypatch: pytest.MonkeyPatch, stub_db: dict, kits: Path
) -> None:
    client = _admin_client(monkeypatch)
    _write_workspace_tree(kits, _WS, "acme")
    resp = client.request(
        "DELETE",
        f"/api/admin/workspaces/{_WS}",
        json={"confirm_workspace_id": _WS},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_row_deleted"] is True
    assert body["disk_removed"] is True
    assert body["brands_removed"] == ["acme"]
    assert body["files_removed"] == 3
    assert stub_db["delete_ws"] == [_WS]
    assert not (kits / _WS).exists()


def test_db_route_503_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # No DATABASE_URL: a DB-backed admin route reports the missing dependency.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = _admin_client(monkeypatch)
    assert client.post(
        "/api/admin/workspaces", json={"workspace_id": _WS, "slug": "x"}
    ).status_code == 503
    assert client.get("/api/admin/usage").status_code == 503


# ---------------------------------------------------------------------------
# Postgres tier: real CASCADE + lifecycle + cross-tenant usage
# ---------------------------------------------------------------------------

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

requires_pg = pytest.mark.skipif(
    not (_RUN and _URL),
    reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
)


@requires_pg
@pytest.mark.asyncio
async def test_delete_workspace_cascades_all_tenant_data() -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session
    from brand_ops.db.bootstrap import ensure_membership, ensure_workspace

    ws = str(uuid.uuid4())
    user = f"user-{uuid.uuid4()}"
    try:
        await ensure_workspace(ws, f"ws-{ws[:8]}")
        await ensure_membership(user_key=user, workspace_id=ws, role="owner", is_default=True)
        sm = get_sessionmaker()
        # Seed a brand, an asset, and a spend event in the workspace.
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                await repo.upsert_brand(session, ws, slug="acme", display_name="Acme")
                await repo.create_asset(
                    session, ws, brand_slug="acme", asset_type="logo", mime="image/png"
                )
                await repo.record_spend_event(
                    session,
                    ws,
                    agent_kind="specialist",
                    agent_ref="brand_intake",
                    kind="chat",
                    cost_cents=1.5,
                )

        # Delete the workspace anchor; the CASCADE should remove everything.
        async with sm() as session, session.begin():
            assert await repo.delete_workspace(session, ws) is True

        # Every scoped table is now empty for that workspace, and the membership
        # row is gone too (it cascades from the workspace).
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                assert await repo.list_brands(session, ws) == []
                assert await repo.list_assets(session, ws) == []
                bc, ac, spend = await repo.workspace_counts(session, ws)
                assert (bc, ac, spend) == (0, 0, 0.0)
            async with scoped_session(session, user_key=user):
                assert list(await repo.list_user_memberships(session, user)) == []

        # A second delete is a no-op (idempotent).
        async with sm() as session, session.begin():
            assert await repo.delete_workspace(session, ws) is False
    finally:
        await dispose_engine()


@requires_pg
@pytest.mark.asyncio
async def test_provisioning_lifecycle_create_grant_revoke() -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session

    ws = str(uuid.uuid4())
    user = f"user-{uuid.uuid4()}"
    try:
        sm = get_sessionmaker()
        # Create the workspace via the repo path the admin route uses.
        async with sm() as session, session.begin():
            await repo.upsert_workspace(session, workspace_id=ws, slug=f"ws-{ws[:8]}")
        # Grant the user.
        async with sm() as session, scoped_session(session, user_key=user):
            await repo.upsert_membership(
                session, user_key=user, workspace_id=ws, role="member"
            )
        async with sm() as session, scoped_session(session, user_key=user):
            mine = await repo.list_user_memberships(session, user)
            assert [str(r.workspace_id) for r in mine] == [ws]
        # Revoke (scoped to the member, as the admin route does, so the
        # user-scoped FORCE-RLS policy admits the delete) and confirm it is gone.
        async with sm() as session, scoped_session(session, user_key=user):
            assert await repo.delete_membership(session, workspace_id=ws, user_key=user) is True
            assert await repo.delete_membership(session, workspace_id=ws, user_key=user) is False
        async with sm() as session, scoped_session(session, user_key=user):
            assert list(await repo.list_user_memberships(session, user)) == []
    finally:
        await dispose_engine()


@requires_pg
@pytest.mark.asyncio
async def test_cross_tenant_usage_counts_each_workspace() -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session
    from brand_ops.db.bootstrap import ensure_workspace

    ws1 = str(uuid.uuid4())
    ws2 = str(uuid.uuid4())
    try:
        await ensure_workspace(ws1, f"ws-{ws1[:8]}")
        await ensure_workspace(ws2, f"ws-{ws2[:8]}")
        sm = get_sessionmaker()
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws1):
                await repo.upsert_brand(session, ws1, slug="a", display_name="A")
                await repo.create_asset(
                    session, ws1, brand_slug="a", asset_type="logo", mime="image/png"
                )
            # ws2 left empty.

        # The route's algorithm: enumerate the anchor table (no GUC), then count
        # each workspace under its own scope.
        seen: dict[str, tuple[int, int, float]] = {}
        async with sm() as session:
            async with session.begin():
                workspaces = await repo.list_all_workspaces(session)
            for row in workspaces:
                wid = str(row.workspace_id)
                if wid not in (ws1, ws2):
                    continue  # ignore any other tenants on a shared test DB
                async with scoped_session(session, workspace_id=wid):
                    seen[wid] = await repo.workspace_counts(session, wid)
        assert seen[ws1] == (1, 1, 0.0)
        assert seen[ws2] == (0, 0, 0.0)
    finally:
        await dispose_engine()
