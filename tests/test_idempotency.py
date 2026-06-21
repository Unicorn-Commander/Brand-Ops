"""Idempotency-Key claim/replay/conflict, unit + Postgres-backed.

The pure helpers (fingerprint, the no-workspace/no-DB skip) need nothing. The
claim/replay/conflict/in-progress flow and the per-workspace RLS isolation need a
real Postgres, wired the same way the other DB tests need it:

    BRAND_OPS_RUN_DB_TESTS=1 \\
    DATABASE_URL=postgresql+asyncpg://brand_ops_app:apppw@127.0.0.1:55433/brand_ops_test \\
    uv run pytest tests/test_idempotency.py

The sync public API (begin_sync/complete_sync/release_sync) drives its own
``asyncio.run`` loop, so the DB tests call it through ``asyncio.to_thread`` to run
it OFF the test's event loop (calling it inline would hit its running-loop skip).
Each test uses fresh uuids so runs never collide and need no teardown.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from brand_ops.idempotency import (
    begin_sync as idem_begin,
)
from brand_ops.idempotency import (
    complete_sync as idem_complete,
)
from brand_ops.idempotency import (
    fingerprint as idem_fingerprint,
)
from brand_ops.idempotency import (
    release_sync as idem_release,
)

# --- pure helpers: no database ----------------------------------------------


def test_fingerprint_is_stable_and_key_order_independent() -> None:
    a = idem_fingerprint("/api/generate", {"brand": "acme", "asset": "logo"})
    b = idem_fingerprint("/api/generate", {"asset": "logo", "brand": "acme"})
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_fingerprint_differs_by_path_and_by_body() -> None:
    base = idem_fingerprint("/api/generate", {"brand": "acme"})
    assert base != idem_fingerprint("/api/generate-card", {"brand": "acme"})
    assert base != idem_fingerprint("/api/generate", {"brand": "other"})


def test_begin_skips_without_workspace_or_key() -> None:
    # No tenant or no key -> idempotency is a no-op, the caller proceeds.
    assert idem_begin(None, "k", "fp").skip is True
    assert idem_begin("ws", None, "fp").skip is True
    assert idem_begin(None, "k", "fp").claimed is False


# --- the claim flow: needs Postgres -----------------------------------------

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


_FP = idem_fingerprint("/api/generate", {"brand": "acme", "asset": "logo"})


@_db
@pytest.mark.asyncio
async def test_claim_then_complete_then_replay() -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)

    first = await asyncio.to_thread(idem_begin, ws, "k-replay", _FP)
    assert first.claimed is True

    body = {"brand": "acme", "asset": "logo", "files": [{"path": "a.png"}]}
    await asyncio.to_thread(idem_complete, ws, "k-replay", status=200, body=body)

    again = await asyncio.to_thread(idem_begin, ws, "k-replay", _FP)
    assert again.claimed is False
    assert again.replay == body
    assert again.replay_status == 200


@_db
@pytest.mark.asyncio
async def test_second_claim_while_in_progress() -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)

    assert (await asyncio.to_thread(idem_begin, ws, "k-busy", _FP)).claimed is True
    # Not yet completed: a repeat is told it is in progress (the route maps -> 409).
    busy = await asyncio.to_thread(idem_begin, ws, "k-busy", _FP)
    assert busy.in_progress is True
    assert busy.claimed is False


@_db
@pytest.mark.asyncio
async def test_same_key_different_request_is_conflict() -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)

    assert (await asyncio.to_thread(idem_begin, ws, "k-diff", _FP)).claimed is True
    other_fp = idem_fingerprint("/api/generate", {"brand": "acme", "asset": "app_icon"})
    clash = await asyncio.to_thread(idem_begin, ws, "k-diff", other_fp)
    assert clash.conflict is True


@_db
@pytest.mark.asyncio
async def test_release_lets_a_retry_reclaim() -> None:
    ws = str(uuid.uuid4())
    await _ensure_ws(ws)

    assert (await asyncio.to_thread(idem_begin, ws, "k-rel", _FP)).claimed is True
    await asyncio.to_thread(idem_release, ws, "k-rel")
    # The failed attempt released its claim, so the same key can be claimed again.
    assert (await asyncio.to_thread(idem_begin, ws, "k-rel", _FP)).claimed is True


@_db
@pytest.mark.asyncio
async def test_keys_are_rls_isolated_between_workspaces() -> None:
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    await _ensure_ws(ws_a)
    await _ensure_ws(ws_b)

    # The same key string in two tenants is two independent claims (unique is
    # per (workspace_id, idem_key)); neither sees or blocks the other.
    assert (await asyncio.to_thread(idem_begin, ws_a, "shared", _FP)).claimed is True
    assert (await asyncio.to_thread(idem_begin, ws_b, "shared", _FP)).claimed is True
