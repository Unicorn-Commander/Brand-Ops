"""Data-retention soft-delete + sweeper, unit + Postgres-backed.

Two layers, mirroring the rest of the suite:

* The unit layer is network-, DB-, and LLM-free. It proves a soft-deleted asset
  (a sidecar with ``deleted_at`` set) is hidden from the file-walk read path, and
  that the sweeper is DISABLED BY DEFAULT -- ``retention_days()`` is 0 and a sweep
  with no window purges nothing.
* The DB layer exercises the real ``asset`` row path -- the ``deleted_at`` filter
  in ``list_assets``, ``soft_delete_asset``, the ``list_soft_deleted_before``
  cutoff, and ``purge_asset`` -- which a sidecar-only test cannot. It is wired the
  same way the other DB tests are:

    BRAND_OPS_RUN_DB_TESTS=1 \\
    DATABASE_URL=postgresql+asyncpg://brand_ops_app:apppw@127.0.0.1:55433/brand_ops_test \\
    uv run pytest tests/test_retention.py

The sync ``soft_delete_sync`` drives its own ``asyncio.run`` loop, so a DB test
that called it would hit its running-loop skip; the DB tests drive the async
repository helpers directly through a ``workspace_session`` instead. Each test
uses fresh uuids so runs never collide and need no teardown.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# --- unit layer: no database ------------------------------------------------


def _write_sidecar(
    kits_dir: Path,
    slug: str,
    asset_type: str,
    fname: str,
    *,
    kind: str = "generated",
    **overrides,
) -> Path:
    """Drop a placeholder image + a catalog sidecar into a tmp kit. Returns sidecar."""
    asset_dir = kits_dir / slug / kind / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image = asset_dir / fname
    image.write_bytes(b"\x89PNG\r\n")
    data = {
        "brand": slug,
        "asset_type": asset_type,
        "variant": None,
        "backend": "test",
        "model": None,
        "mime": "image/png",
        "size": [64, 64],
        "transparent": False,
        "cost_cents": 0.0,
        "created_at": "2026-01-01-00-00-00",
        "prompt": None,
        "lifecycle_status": "approved",
    }
    data.update(overrides)
    sidecar = image.with_suffix(image.suffix + ".json")
    sidecar.write_text(json.dumps(data, indent=2))
    return sidecar


def test_coerce_record_hides_a_soft_deleted_sidecar() -> None:
    # A sidecar carrying a truthy ``deleted_at`` is not a live catalog record.
    from brand_ops.api.catalog import _coerce_record

    live = {"asset_type": "logo", "brand": "acme"}
    deleted = {"asset_type": "logo", "brand": "acme", "deleted_at": "2026-06-21T00:00:00+00:00"}
    img = Path("x.png")
    assert _coerce_record(live, Path("x.png.json"), img, kind="generated", brand="acme") is not None
    assert _coerce_record(deleted, Path("x.png.json"), img, kind="generated", brand="acme") is None


def test_read_assets_hides_soft_deleted_sidecars(tmp_path: Path) -> None:
    # One normal + one soft-deleted sidecar under the same brand: only the normal
    # one comes back from the file-walk read path.
    from brand_ops.api.catalog import read_assets

    _write_sidecar(tmp_path, "acme", "logo", "live.png")
    _write_sidecar(
        tmp_path, "acme", "logo", "gone.png", deleted_at="2026-06-21T00:00:00+00:00"
    )

    records = read_assets(brand="acme", kits_dir=tmp_path)
    files = {r.file for r in records}
    assert files == {"live.png"}


def test_retention_days_defaults_to_zero(monkeypatch) -> None:
    from brand_ops import retention_sweep

    monkeypatch.delenv("BRAND_OPS_RETENTION_DAYS", raising=False)
    assert retention_sweep.retention_days() == 0
    # A blank, non-integer, or negative value also reads as disabled.
    monkeypatch.setenv("BRAND_OPS_RETENTION_DAYS", "")
    assert retention_sweep.retention_days() == 0
    monkeypatch.setenv("BRAND_OPS_RETENTION_DAYS", "notanumber")
    assert retention_sweep.retention_days() == 0
    monkeypatch.setenv("BRAND_OPS_RETENTION_DAYS", "-5")
    assert retention_sweep.retention_days() == 0
    monkeypatch.setenv("BRAND_OPS_RETENTION_DAYS", "30")
    assert retention_sweep.retention_days() == 30


def test_sweep_disabled_by_default_purges_nothing(tmp_path: Path, monkeypatch) -> None:
    # Retention disabled (the default): the CLI exits 0, and even a soft-deleted
    # on-disk asset is left untouched -- the sweep never destroys data without a
    # configured window.
    from brand_ops import retention_sweep

    monkeypatch.delenv("BRAND_OPS_RETENTION_DAYS", raising=False)
    sidecar = _write_sidecar(
        tmp_path / str(uuid.uuid4()),
        "acme",
        "logo",
        "gone.png",
        deleted_at="2020-01-01T00:00:00+00:00",
    )
    image = sidecar.with_suffix("")  # strip the .json

    rc = retention_sweep.main(["--dry-run", "--kits-dir", str(tmp_path)])
    assert rc == 0
    # Disabled means sweep() short-circuits to {} and touches nothing.
    assert asyncio.run(retention_sweep.sweep(tmp_path)) == {}
    rc = retention_sweep.main(["--kits-dir", str(tmp_path)])
    assert rc == 0
    assert image.exists() and sidecar.exists()


# --- DB layer: needs Postgres -----------------------------------------------

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")
_db = pytest.mark.skipif(
    not (_RUN and _URL),
    reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
)


@pytest.fixture(autouse=True)
def _dispose_engine_after_each():
    yield
    if _RUN and _URL:
        from brand_ops.db.base import dispose_engine

        asyncio.run(dispose_engine())


async def _ensure_ws(ws_id: str) -> None:
    from brand_ops.db.bootstrap import ensure_workspace

    await ensure_workspace(ws_id, f"ws-{ws_id[:8]}")


def _sidecar(
    kits_root: Path, ws_id: str, slug: str, kind: str, asset_type: str, fname: str
) -> Path:
    """A sidecar laid out at ``<kits>/<ws>/<slug>/<kind>/<asset_type>/<fname>``."""
    asset_dir = kits_root / ws_id / slug / kind / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image = asset_dir / fname
    image.write_bytes(b"\x89PNG\r\n")
    data = {
        "brand": slug,
        "asset_type": asset_type,
        "backend": "test",
        "mime": "image/png",
        "size": [64, 64],
        "transparent": False,
        "cost_cents": 0.0,
        "created_at": "2026-06-20-14-30-00",
        "lifecycle_status": "approved",
    }
    sidecar = image.with_suffix(image.suffix + ".json")
    sidecar.write_text(json.dumps(data, indent=2))
    return sidecar


async def _seed_asset(
    ws_id: str, kits_root: Path, slug: str, kind: str, asset_type: str, fname: str
) -> str:
    """Index one fresh sidecar as an asset row; return its storage_key.

    The storage_key the row is unique on is the file path relative to the
    workspace root: ``<slug>/<kind>/<asset_type>/<fname>``.
    """
    from brand_ops.catalog_index import upsert_sidecar
    from brand_ops.db.base import get_sessionmaker, workspace_session

    sidecar = _sidecar(kits_root, ws_id, slug, kind, asset_type, fname)
    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws_id) as scoped:
            ok = await upsert_sidecar(
                scoped,
                ws_id,
                sidecar_path=sidecar,
                workspace_root=kits_root / ws_id,
                kind=kind,
            )
    assert ok is True
    return f"{slug}/{kind}/{asset_type}/{fname}"


async def _list(ws_id: str, **kw):
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, workspace_session

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws_id) as scoped:
            return list(await repo.list_assets(scoped, ws_id, **kw))


@_db
@pytest.mark.asyncio
async def test_soft_delete_hides_from_list_assets(tmp_path: Path) -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, workspace_session

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    key = await _seed_asset(ws, tmp_path, "acme", "generated", "logo", "x.png")

    assert len(await _list(ws)) == 1  # live: visible

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws) as scoped:
            assert await repo.soft_delete_asset(scoped, ws, storage_key=key) is True
            # A repeat soft-delete is a no-op (already tombstoned).
            assert await repo.soft_delete_asset(scoped, ws, storage_key=key) is False

    assert await _list(ws) == []  # soft-deleted: hidden from the catalog


@_db
@pytest.mark.asyncio
async def test_list_soft_deleted_before_respects_the_cutoff(tmp_path: Path) -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, workspace_session

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    key = await _seed_asset(ws, tmp_path, "acme", "generated", "logo", "x.png")

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws) as scoped:
            await repo.soft_delete_asset(scoped, ws, storage_key=key)

    now = datetime.now(UTC)
    future = now + timedelta(days=1)
    past = now - timedelta(days=1)

    # The tombstone is ~now: a future cutoff finds it, a past cutoff does not.
    async with sm() as session:
        async with workspace_session(session, ws) as scoped:
            found = await repo.list_soft_deleted_before(scoped, ws, future)
            assert len(found) == 1 and found[0].storage_key == key
            assert await repo.list_soft_deleted_before(scoped, ws, past) == []


@_db
@pytest.mark.asyncio
async def test_purge_asset_removes_the_row(tmp_path: Path) -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, workspace_session

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    key = await _seed_asset(ws, tmp_path, "acme", "generated", "logo", "x.png")
    far_future = datetime.now(UTC) + timedelta(days=1)

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws) as scoped:
            await repo.soft_delete_asset(scoped, ws, storage_key=key)
            rows = await repo.list_soft_deleted_before(scoped, ws, far_future)
            assert len(rows) == 1
            await repo.purge_asset(scoped, ws, rows[0].id)
            # Gone for good: no longer a sweep candidate either.
            assert await repo.list_soft_deleted_before(scoped, ws, far_future) == []
