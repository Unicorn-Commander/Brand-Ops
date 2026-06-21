"""Postgres-backed tests for the asset catalog source-of-truth (the dual-write +
backfill that move the catalog from on-disk sidecars into the DB).

These exercise the real database path -- the ``asset`` upsert, its
``(workspace_id, storage_key)`` idempotency key, RLS tenant isolation on the new
rows, and the backfill walker -- which a sidecar-only test cannot. SKIPPED unless
a Postgres is wired the same way ``test_db_tenancy`` needs it:

    BRAND_OPS_RUN_DB_TESTS=1 \\
    DATABASE_URL=postgresql+asyncpg://brand_ops_app:apppw@127.0.0.1:55433/brand_ops_test \\
    uv run pytest tests/test_catalog_sot.py

Each test uses fresh uuids so runs never collide and need no teardown.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        not (_RUN and _URL),
        reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
    ),
    pytest.mark.asyncio,
]


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_each():
    """Dispose the process-wide engine after each test (own-loop pooling); see
    test_db_tenancy for the rationale."""
    yield
    from brand_ops.db.base import dispose_engine

    await dispose_engine()


def _sidecar(
    kits_root: Path,
    ws_id: str,
    slug: str,
    kind: str,
    asset_type: str,
    fname: str,
    **overrides,
) -> Path:
    """Write one sidecar (and a placeholder image) under a workspace subtree.

    Lays the file out exactly where the catalog reader and the dual-write expect
    it -- ``<kits>/<ws>/<slug>/<kind>/<asset_type>/<fname>`` -- and drops the
    ``<fname>.json`` sidecar beside it. Returns the sidecar path.
    """
    asset_dir = kits_root / ws_id / slug / kind / asset_type
    asset_dir.mkdir(parents=True, exist_ok=True)
    image = asset_dir / fname
    image.write_bytes(b"\x89PNG\r\n")  # a token of bytes; the index reads the json
    data = {
        "brand": slug,
        "asset_type": asset_type,
        "variant": None,
        "backend": "litellm",
        "model": "google/gemini-3.1-flash-image-preview",
        "mime": "image/png",
        "size": [512, 512],
        "transparent": False,
        "cost_cents": 0.42,
        "prompt": "a mark",
        "reference_images": ["imported/logo/ref.png"],
        "created_at": "2026-06-20-14-30-00",
        "lifecycle_status": "approved",
        "compliance": {"status": "pass", "score": 100, "violations": []},
    }
    data.update(overrides)
    sidecar = image.with_suffix(image.suffix + ".json")
    sidecar.write_text(json.dumps(data, indent=2))
    return sidecar


async def _ensure_ws(ws_id: str) -> None:
    from brand_ops.db.bootstrap import ensure_workspace

    await ensure_workspace(ws_id, f"ws-{ws_id[:8]}")


async def _scoped_upsert(ws_id: str, kits_root: Path, sidecar: Path, kind: str) -> bool:
    from brand_ops.catalog_index import upsert_sidecar
    from brand_ops.db.base import get_sessionmaker, workspace_session

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws_id) as scoped:
            return await upsert_sidecar(
                scoped,
                ws_id,
                sidecar_path=sidecar,
                workspace_root=kits_root / ws_id,
                kind=kind,
            )


async def _list(ws_id: str, **kw):
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, workspace_session

    sm = get_sessionmaker()
    async with sm() as session:
        async with workspace_session(session, ws_id) as scoped:
            return list(await repo.list_assets(scoped, ws_id, **kw))


# --- the dual-write core ----------------------------------------------------


async def test_upsert_indexes_sidecar_fields(tmp_path: Path) -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    sc = _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")

    assert await _scoped_upsert(ws, tmp_path, sc, "generated") is True

    rows = await _list(ws)
    assert len(rows) == 1
    a = rows[0]
    assert a.brand_slug == "acme"
    assert a.asset_type == "logo"
    assert a.kind == "generated"
    assert a.storage_key == "acme/generated/logo/x.png"
    assert a.width == 512 and a.height == 512
    assert a.transparent is False
    assert a.model == "google/gemini-3.1-flash-image-preview"
    assert a.lifecycle_status == "approved"
    assert a.compliance["status"] == "pass"
    assert a.created_label == "2026-06-20-14-30-00"
    assert a.reference_images == ["imported/logo/ref.png"]
    assert float(a.cost_cents) == pytest.approx(0.42)


async def test_upsert_is_idempotent_and_refreshes(tmp_path: Path) -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    sc = _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")

    await _scoped_upsert(ws, tmp_path, sc, "generated")
    await _scoped_upsert(ws, tmp_path, sc, "generated")  # same key, second time
    rows = await _list(ws)
    assert len(rows) == 1  # upsert on (workspace_id, storage_key), not a duplicate

    # A re-stamp changes the sidecar; re-indexing refreshes the row in place.
    sc = _sidecar(
        tmp_path, ws, "acme", "generated", "logo", "x.png", lifecycle_status="needs_review"
    )
    await _scoped_upsert(ws, tmp_path, sc, "generated")
    rows = await _list(ws)
    assert len(rows) == 1
    assert rows[0].lifecycle_status == "needs_review"


async def test_rows_are_rls_isolated_between_workspaces(tmp_path: Path) -> None:
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    await _ensure_ws(ws_a)
    await _ensure_ws(ws_b)

    sc = _sidecar(tmp_path, ws_a, "acme", "generated", "logo", "x.png")
    await _scoped_upsert(ws_a, tmp_path, sc, "generated")

    assert len(await _list(ws_a)) == 1
    # Scoped to B, A's asset is invisible under row level security.
    assert await _list(ws_b) == []


async def test_same_storage_key_is_per_workspace(tmp_path: Path) -> None:
    # The unique key is (workspace_id, storage_key): the identical relative path
    # may exist in two tenants without colliding.
    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    await _ensure_ws(ws_a)
    await _ensure_ws(ws_b)

    sc_a = _sidecar(tmp_path, ws_a, "acme", "generated", "logo", "x.png")
    sc_b = _sidecar(tmp_path, ws_b, "acme", "generated", "logo", "x.png")
    assert await _scoped_upsert(ws_a, tmp_path, sc_a, "generated") is True
    assert await _scoped_upsert(ws_b, tmp_path, sc_b, "generated") is True

    rows_a, rows_b = await _list(ws_a), await _list(ws_b)
    assert len(rows_a) == 1 and len(rows_b) == 1
    assert rows_a[0].storage_key == rows_b[0].storage_key == "acme/generated/logo/x.png"


# --- the backfill walker ----------------------------------------------------


async def test_backfill_indexes_all_workspaces(tmp_path: Path) -> None:
    from brand_ops.catalog_backfill import backfill

    ws_a, ws_b = str(uuid.uuid4()), str(uuid.uuid4())
    await _ensure_ws(ws_a)
    await _ensure_ws(ws_b)

    _sidecar(tmp_path, ws_a, "acme", "generated", "logo", "a1.png")
    _sidecar(tmp_path, ws_a, "acme", "imported", "logo", "a2.png")
    _sidecar(tmp_path, ws_b, "beta", "generated", "og_image", "b1.png")
    # A non-uuid top-level dir (the shared template) must be ignored entirely.
    (tmp_path / "_template" / "x" / "generated" / "logo").mkdir(parents=True)

    results = await backfill(tmp_path)

    assert results[ws_a]["indexed"] == 2
    assert results[ws_b]["indexed"] == 1
    assert "_template" not in results
    assert len(await _list(ws_a)) == 2
    assert len(await _list(ws_b, brand="beta", asset_type="og_image")) == 1


async def test_backfill_dry_run_writes_nothing(tmp_path: Path) -> None:
    from brand_ops.catalog_backfill import backfill

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")

    results = await backfill(tmp_path, dry_run=True)
    assert results[ws]["indexed"] == 1
    assert await _list(ws) == []  # dry-run counted but wrote nothing


async def test_backfill_skips_workspace_without_anchor_row(tmp_path: Path) -> None:
    from brand_ops.catalog_backfill import backfill

    ws = str(uuid.uuid4())  # deliberately NOT ensured: no anchor row
    _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")

    results = await backfill(tmp_path)
    # -1 is the "no anchor row, skipped" sentinel; nothing was written.
    assert results[ws]["skipped"] == -1
    assert results[ws]["indexed"] == 0


async def test_backfill_is_rerunnable(tmp_path: Path) -> None:
    from brand_ops.catalog_backfill import backfill

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")

    await backfill(tmp_path)
    await backfill(tmp_path)  # second pass upserts, never duplicates
    assert len(await _list(ws)) == 1


# --- the dual-read (flag-gated, end to end through read_assets) --------------


async def test_dual_read_serves_db_records_when_flag_on(
    tmp_path: Path, monkeypatch
) -> None:
    # Index an asset, scope the request to its workspace, flip the SoR flag on, and
    # read through the real read_assets. read_assets is sync and bridges to the DB
    # with asyncio.run, so it must run OFF the test's event loop -- to_thread both
    # gives it a loop-free thread and propagates the contextvars scope. The sidecar
    # fallback here would read DEFAULT_KITS_DIR/<random-ws> (absent) -> [], so a
    # non-empty result can only have come from Postgres.
    from brand_ops.api.catalog import read_assets
    from brand_ops.brand_loader import reset_workspace_scope, set_workspace_scope

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    sc = _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")
    await _scoped_upsert(ws, tmp_path, sc, "generated")

    monkeypatch.setenv("BRAND_OPS_CATALOG_SOT", "db")
    token = set_workspace_scope(ws, frozenset({"acme"}))
    try:
        records = await asyncio.to_thread(read_assets, brand="acme", asset="logo")
    finally:
        reset_workspace_scope(token)

    assert len(records) == 1
    r = records[0]
    assert r.brand == "acme" and r.asset_type == "logo"
    assert r.file == "x.png"
    assert r.kind == "generated"
    assert r.lifecycle_status == "approved"
    assert r.url and "brand=acme" in r.url


async def test_dual_read_off_by_default_uses_sidecars(
    tmp_path: Path, monkeypatch
) -> None:
    # Same DB state, flag OFF (the default): read_assets takes the file walk, which
    # for this random workspace's (absent) default volume yields nothing -- proving
    # the DB rows are NOT consulted unless the flag is set.
    from brand_ops.api.catalog import read_assets
    from brand_ops.brand_loader import reset_workspace_scope, set_workspace_scope

    ws = str(uuid.uuid4())
    await _ensure_ws(ws)
    sc = _sidecar(tmp_path, ws, "acme", "generated", "logo", "x.png")
    await _scoped_upsert(ws, tmp_path, sc, "generated")

    monkeypatch.delenv("BRAND_OPS_CATALOG_SOT", raising=False)
    token = set_workspace_scope(ws, frozenset({"acme"}))
    try:
        records = await asyncio.to_thread(read_assets, brand="acme", asset="logo")
    finally:
        reset_workspace_scope(token)

    assert records == []
