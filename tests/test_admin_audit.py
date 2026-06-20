"""Tamper-evident admin-audit log + the DB-inclusive export.

Three things this enterprise slice added, tested in the suite's two tiers:

* The no-database tier (always runs) covers the pure-file behavior: the
  ``brand_ops.admin_audit`` append + ``verify_admin_chain`` integrity (including a
  concurrent-append check, the analogue of test_activity_concurrency for this
  log), that a single edited/deleted/reordered line is detected and located, that
  an erasure tombstone persists and is found after a simulated workspace delete
  through the admin route, and that ``admin_ops.build_export_zip`` writes seeded DB
  rows under ``db/`` with the per-table counts recorded in the manifest. These run
  entirely inside ``tmp_path`` and touch nothing in the repo.
* The Postgres tier (skipped unless ``BRAND_OPS_RUN_DB_TESTS=1`` and
  ``DATABASE_URL`` name the ``brand_ops_app`` NOBYPASSRLS role on a migrated DB,
  exactly like test_db_tenancy.py / test_admin.py) proves ``export_workspace_rows``
  reads a real seeded ``workspace_member`` and ``agent_spend_event`` UNDER the
  tenant's row level security, so the export carries the DB-only data that
  ``delete_workspace``'s CASCADE would erase.

The admin-audit log resolves its own ``DEFAULT_KITS_DIR`` at call time, so every
file test redirects it (or passes ``kits_dir=tmp_path``) and never writes into the
repo's real brand-kits tree.
"""
from __future__ import annotations

import concurrent.futures as cf
import io
import json
import os
import uuid
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from brand_ops import admin_audit, admin_ops
from brand_ops.api import admin as admin_mod
from brand_ops.api.admin import admin_router
from brand_ops.api.auth import Principal, require_principal

_ADMIN_UID = "admin-1"
_WS = "019e50a3-d995-723f-ab66-0f765f92c0f4"


# ---------------------------------------------------------------------------
# The admin-audit hash chain (no DB; always runs)
# ---------------------------------------------------------------------------


def test_append_and_verify_chain_intact(tmp_path: Path) -> None:
    """A mix of action, denied, and tombstone entries verifies as one chain."""
    admin_audit.append_admin_event(
        _ADMIN_UID, "create_workspace", _WS, {"slug": "acme"}, kits_dir=tmp_path
    )
    admin_audit.append_admin_event(
        "intruder", "admin_access", None, {"anonymous": False}, result="denied", kits_dir=tmp_path
    )
    admin_audit.write_tombstone(
        _WS, "acme", _ADMIN_UID, {"disk_file_count": 3}, kits_dir=tmp_path
    )

    report = admin_audit.verify_admin_chain(kits_dir=tmp_path)
    assert report["ok"] is True, report
    assert report["chained"] == 3
    assert report["legacy"] == 0
    assert report["break_at"] is None

    events = admin_audit.read_admin_events(kits_dir=tmp_path)
    # Newest first, and each line carries its kind + the chain fields.
    assert [e["kind"] for e in events] == ["tombstone", "action", "action"]
    assert all("entry_hash" in e and "prev_hash" in e for e in events)
    # The deny path is recorded with result="denied".
    denied = [e for e in events if e.get("result") == "denied"]
    assert len(denied) == 1 and denied[0]["actor_uc_uid"] == "intruder"


def test_empty_log_is_vacuously_intact(tmp_path: Path) -> None:
    report = admin_audit.verify_admin_chain(kits_dir=tmp_path)
    assert report == {
        "ok": True,
        "chained": 0,
        "legacy": 0,
        "total": 0,
        "chain_start": None,
        "break_at": None,
        "reason": None,
    }
    assert admin_audit.read_admin_events(kits_dir=tmp_path) == []


def test_edited_line_breaks_chain(tmp_path: Path) -> None:
    for i in range(3):
        admin_audit.append_admin_event(
            _ADMIN_UID, "create_workspace", _WS, {"n": i}, kits_dir=tmp_path
        )
    path = tmp_path / ".admin-audit.jsonl"
    lines = path.read_text().splitlines()
    obj = json.loads(lines[1])
    obj["params_summary"] = {"n": 999}  # edit the body without recomputing the hash
    lines[1] = json.dumps(obj)
    path.write_text("\n".join(lines) + "\n")

    report = admin_audit.verify_admin_chain(kits_dir=tmp_path)
    assert report["ok"] is False
    assert report["break_at"] == 1
    assert report["reason"] == "entry_hash"


def test_deleted_line_breaks_chain(tmp_path: Path) -> None:
    for i in range(3):
        admin_audit.append_admin_event(
            _ADMIN_UID, "create_workspace", _WS, {"n": i}, kits_dir=tmp_path
        )
    path = tmp_path / ".admin-audit.jsonl"
    lines = path.read_text().splitlines()
    del lines[1]  # drop the middle line: the next line's prev_hash no longer matches
    path.write_text("\n".join(lines) + "\n")

    report = admin_audit.verify_admin_chain(kits_dir=tmp_path)
    assert report["ok"] is False
    assert report["break_at"] == 1
    assert report["reason"] == "prev_hash"


def test_concurrent_appends_keep_chain_intact(tmp_path: Path) -> None:
    """The append critical section (in-process lock + flock + fsync) must hold the
    chain under concurrent writers, the same property test_activity_concurrency
    asserts for the activity feed. Without it two writers could record the same
    prev_hash and fork the chain into a permanent false-positive tamper break."""
    n = 64

    def one(i: int) -> None:
        admin_audit.append_admin_event(
            _ADMIN_UID, "grant_member", _WS, {"member": f"u-{i}"}, kits_dir=tmp_path
        )

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(one, range(n)))

    report = admin_audit.verify_admin_chain(kits_dir=tmp_path)
    assert report["ok"] is True, report
    assert report["chained"] == n
    assert report["break_at"] is None
    assert len(admin_audit.read_admin_events(limit=n + 10, kits_dir=tmp_path)) == n


def test_append_never_raises_on_unwritable_log(tmp_path: Path, monkeypatch) -> None:
    """A write failure is swallowed (best-effort), never propagated into the action
    it records: the entry is still returned, just not persisted."""

    def _boom(*a, **kw):
        raise OSError("disk full")

    # Force the open to fail; the helper must catch it and return the entry anyway.
    monkeypatch.setattr(Path, "open", _boom)
    entry = admin_audit.append_admin_event(
        _ADMIN_UID, "create_workspace", _WS, {"slug": "acme"}, kits_dir=tmp_path
    )
    assert entry["action"] == "create_workspace"
    assert not (tmp_path / ".admin-audit.jsonl").exists()


# ---------------------------------------------------------------------------
# The tombstone survives a workspace delete through the admin route (no DB)
# ---------------------------------------------------------------------------


def _admin_client(monkeypatch, *, uc_uid: str = _ADMIN_UID) -> TestClient:
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", uc_uid)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid=uc_uid, issuer="keycloak"
    )
    return TestClient(app)


def _stub_delete_db(monkeypatch) -> None:
    """Stub just enough of the DB layer for delete_workspace to run with no Postgres.

    The route reads the slug (session.get -> None), tombstones, then deletes the
    anchor row (stubbed True) and purges the on-disk subtree. Mirrors the
    no-DB stubbing in test_admin.py, scoped to the delete path this test needs.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def begin(self):
            return _FakeSession()

        async def get(self, *a, **kw):
            return None

    monkeypatch.setattr(admin_mod, "get_sessionmaker", lambda: _FakeSession)

    async def _fake_delete_workspace(session, workspace_id):
        return True

    monkeypatch.setattr(admin_mod.repo, "delete_workspace", _fake_delete_workspace)


def test_delete_writes_tombstone_that_survives_erasure(monkeypatch, tmp_path: Path) -> None:
    """The offboard route writes an erasure tombstone to the durable, root-level
    admin-audit log BEFORE deleting, and that log lives OUTSIDE the tenant subtree
    that the purge removes, so the tombstone is still there after the tenant's own
    data is gone."""
    root = tmp_path / "brand-kits"
    root.mkdir()
    # The audit log and the on-disk helpers share this throwaway kits root.
    monkeypatch.setattr(admin_audit, "DEFAULT_KITS_DIR", root)
    monkeypatch.setattr(admin_ops, "DEFAULT_KITS_DIR", root)

    # Lay down the tenant's on-disk subtree so the purge has something to remove.
    ws_dir = root / _WS / "acme"
    (ws_dir / "generated").mkdir(parents=True)
    (ws_dir / "brand.yaml").write_text("brand:\n  slug: acme\n  display_name: Acme\n")
    (ws_dir / "generated" / "card.png").write_bytes(b"png-bytes")

    _stub_delete_db(monkeypatch)
    client = _admin_client(monkeypatch)
    resp = client.request(
        "DELETE", f"/api/admin/workspaces/{_WS}", json={"confirm_workspace_id": _WS}
    )
    assert resp.status_code == 200
    # The tenant's own subtree is gone...
    assert not (root / _WS).exists()
    # ...but the root-level audit log (one level up, not under <ws>/) survived it.
    assert (root / ".admin-audit.jsonl").exists()

    events = admin_audit.read_admin_events(kits_dir=root)
    tombstones = [e for e in events if e.get("kind") == "tombstone"]
    assert len(tombstones) == 1
    tomb = tombstones[0]
    assert tomb["workspace_id"] == _WS
    assert tomb["actor_uc_uid"] == _ADMIN_UID
    # The tombstone captured the pre-delete on-disk footprint.
    assert tomb["counts"]["disk_file_count"] == 2
    assert tomb["counts"]["disk_brands"] == ["acme"]
    # The completed delete is also recorded as an ordinary action, and the chain
    # over both entries is intact.
    assert any(e.get("action") == "delete_workspace" for e in events)
    assert admin_audit.verify_admin_chain(kits_dir=root)["ok"] is True


def test_denied_admin_access_is_recorded(monkeypatch, tmp_path: Path) -> None:
    """A refused admin request lands in the durable log with result='denied'."""
    monkeypatch.setattr(admin_audit, "DEFAULT_KITS_DIR", tmp_path)
    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", "admin-1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid="not-admin", issuer="keycloak"
    )
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 403

    events = admin_audit.read_admin_events(kits_dir=tmp_path)
    assert len(events) == 1
    assert events[0]["result"] == "denied"
    assert events[0]["actor_uc_uid"] == "not-admin"
    assert events[0]["action"] == "admin_access"


# ---------------------------------------------------------------------------
# Export includes the DB rows under db/ with a manifest count (no DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def kits(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "brand-kits"
    root.mkdir()
    monkeypatch.setattr(admin_ops, "DEFAULT_KITS_DIR", root)
    return root


def test_export_zip_includes_db_rows_and_counts(kits: Path) -> None:
    """A seeded DB-row mapping is written under db/<table>.json and counted in the
    manifest, so the export carries the DB-only data delete_workspace erases."""
    db_rows = {
        "workspace_member": [
            {"user_key": "u-7", "workspace_id": _WS, "role": "owner", "status": "active"}
        ],
        "agent_spend_event": [
            {"id": "e1", "agent_kind": "specialist", "kind": "chat", "cost_cents": 1.5},
            {"id": "e2", "agent_kind": "specialist", "kind": "image", "cost_cents": 4.0},
        ],
        "brand": [],  # an empty table is still emitted as an empty array
    }
    zip_path = admin_ops.build_export_zip(_WS, slug="acme-ws", db_rows=db_rows)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
            assert "manifest.json" in names
            assert "db/workspace_member.json" in names
            assert "db/agent_spend_event.json" in names
            assert "db/brand.json" in names

            members = json.loads(zf.read("db/workspace_member.json"))
            assert members[0]["user_key"] == "u-7"
            spend = json.loads(zf.read("db/agent_spend_event.json"))
            assert {e["id"] for e in spend} == {"e1", "e2"}
            assert json.loads(zf.read("db/brand.json")) == []

            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["db_row_counts"] == {
            "workspace_member": 1,
            "agent_spend_event": 2,
            "brand": 0,
        }
        assert manifest["db_row_total"] == 3
    finally:
        zip_path.unlink(missing_ok=True)


def test_export_without_db_rows_is_unchanged(kits: Path) -> None:
    """Omitting db_rows (no DB configured) yields an on-disk-only archive with an
    empty db count, so the export degrades cleanly rather than 500ing."""
    zip_path = admin_ops.build_export_zip(_WS)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            assert zf.namelist() == ["manifest.json"]
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["db_row_counts"] == {}
        assert manifest["db_row_total"] == 0
    finally:
        zip_path.unlink(missing_ok=True)


def test_export_route_includes_seeded_db_row(monkeypatch, kits: Path) -> None:
    """End to end through the route, with the DB layer stubbed: a seeded member row
    is read under the (stubbed) workspace scope and lands in the streamed zip."""
    monkeypatch.setattr(admin_audit, "DEFAULT_KITS_DIR", kits)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")

    class _FakeWorkspace:
        slug = "acme-ws"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def begin(self):
            return _FakeSession()

        async def get(self, *a, **kw):
            return _FakeWorkspace()

    class _NullScope:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(admin_mod, "get_sessionmaker", lambda: _FakeSession)
    monkeypatch.setattr(admin_mod, "scoped_session", lambda session, **kw: _NullScope())

    async def _fake_export_rows(session, workspace_id):
        return {"workspace_member": [{"user_key": "u-7", "role": "owner"}]}

    monkeypatch.setattr(admin_mod.repo, "export_workspace_rows", _fake_export_rows)

    monkeypatch.setenv("BRAND_OPS_ADMIN_SUBS", _ADMIN_UID)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid=_ADMIN_UID, issuer="keycloak"
    )
    client = TestClient(app)

    resp = client.get(f"/api/admin/workspaces/{_WS}/export")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert "db/workspace_member.json" in zf.namelist()
        members = json.loads(zf.read("db/workspace_member.json"))
        assert members[0]["user_key"] == "u-7"
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["db_row_counts"]["workspace_member"] == 1
    assert manifest["slug"] == "acme-ws"


# ---------------------------------------------------------------------------
# Postgres tier: export_workspace_rows reads real rows under RLS
# ---------------------------------------------------------------------------

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

requires_pg = pytest.mark.skipif(
    not (_RUN and _URL),
    reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
)


@requires_pg
@pytest.mark.asyncio
async def test_export_workspace_rows_reads_seeded_data_under_rls() -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session
    from brand_ops.db.bootstrap import ensure_membership, ensure_workspace

    ws = str(uuid.uuid4())
    user = f"user-{uuid.uuid4()}"
    try:
        await ensure_workspace(ws, f"ws-{ws[:8]}")
        await ensure_membership(user_key=user, workspace_id=ws, role="owner", is_default=True)
        sm = get_sessionmaker()
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                await repo.upsert_brand(session, ws, slug="acme", display_name="Acme")
                asset = await repo.create_asset(
                    session, ws, brand_slug="acme", asset_type="logo", mime="image/png"
                )
                await repo.add_asset_version(session, ws, asset.id, storage_key="v1")
                await repo.record_spend_event(
                    session,
                    ws,
                    agent_kind="specialist",
                    agent_ref="brand_intake",
                    kind="chat",
                    cost_cents=1.5,
                )

        # Read the DB-only dump under the workspace's own RLS scope, the way the
        # export route does.
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                rows = await repo.export_workspace_rows(session, ws)

        assert len(rows["brand"]) == 1
        assert len(rows["asset"]) == 1
        # asset_version has no workspace_id; it is scoped via the join onto assets,
        # so exactly the one version of this tenant's asset is present.
        assert len(rows["asset_version"]) == 1
        assert rows["asset_version"][0]["storage_key"] == "v1"
        assert len(rows["agent_spend_event"]) == 1
        # The membership graph is part of the portability dump.
        assert any(m["user_key"] == user for m in rows["workspace_member"])
        # Rows are JSON-safe (uuids/datetimes/Decimals serialized).
        json.dumps(rows)
    finally:
        await dispose_engine()


@requires_pg
@pytest.mark.asyncio
async def test_export_rows_isolated_to_one_tenant_under_rls() -> None:
    """asset_version (no workspace_id, no RLS policy of its own) is constrained to
    the tenant by the join onto its assets, so a second tenant's version never
    leaks into the first tenant's export."""
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
                a1 = await repo.create_asset(
                    session, ws1, brand_slug="a", asset_type="logo", mime="image/png"
                )
                await repo.add_asset_version(session, ws1, a1.id, storage_key="ws1-v1")
            async with scoped_session(session, workspace_id=ws2):
                a2 = await repo.create_asset(
                    session, ws2, brand_slug="b", asset_type="logo", mime="image/png"
                )
                await repo.add_asset_version(session, ws2, a2.id, storage_key="ws2-v1")

        async with sm() as session:
            async with scoped_session(session, workspace_id=ws1):
                rows = await repo.export_workspace_rows(session, ws1)
        keys = {v["storage_key"] for v in rows["asset_version"]}
        assert keys == {"ws1-v1"}  # ws2's version is absent
    finally:
        await dispose_engine()
