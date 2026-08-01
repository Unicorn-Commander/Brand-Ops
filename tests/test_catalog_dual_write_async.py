"""The catalog dual-write must fire from an ASYNC caller, not just a sync one.

Regression pin. ``index_sidecar_sync`` used to detect a running event loop and
return early, so every asset created through the async surfaces (the REST routes
and the HTTP MCP server both run handlers on the loop) wrote its bytes and its
sidecar and then skipped the catalog. With ``BRAND_OPS_CATALOG_SOT=db`` the
catalog IS what the Library reads, so those assets landed on disk and were
invisible in the product -- silently, because the skip logged at debug.

These tests need no database: the bridge sessionmaker is faked, and what is
asserted is that the write path is REACHED (and that a failure is reported, not
swallowed into the dark).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from brand_ops import catalog_index


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_bridge(calls: list[str]):
    """A sessionmaker/workspace_session pair that records that it was reached."""

    def get_bridge_sessionmaker():
        calls.append("sessionmaker")
        return _FakeSession

    class _Scoped:
        def __init__(self, session, workspace_id):
            self._workspace_id = workspace_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    def workspace_session(session, workspace_id):
        return _Scoped(session, workspace_id)

    return get_bridge_sessionmaker, workspace_session


@pytest.fixture()
def sidecar(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    d = root / "acme" / "generated" / "logo"
    d.mkdir(parents=True)
    p = d / "logo.png.json"
    p.write_text(json.dumps({"brand": "acme", "asset_type": "logo"}))
    return p


def _install(monkeypatch, calls: list[str]) -> None:
    import brand_ops.db.base as db_base

    getter, scoper = _fake_bridge(calls)
    monkeypatch.setattr(db_base, "get_bridge_sessionmaker", getter, raising=False)
    monkeypatch.setattr(db_base, "workspace_session", scoper, raising=False)

    async def _upsert(*args, **kwargs):
        calls.append("upsert")
        return True

    monkeypatch.setattr(catalog_index, "upsert_sidecar", _upsert)


def test_dual_write_fires_from_a_running_event_loop(monkeypatch, sidecar: Path) -> None:
    """The bug: an async caller silently skipped the catalog write entirely."""
    calls: list[str] = []
    _install(monkeypatch, calls)

    async def caller() -> None:
        # Exactly the shape of a REST route / HTTP MCP tool: on the event loop.
        catalog_index.index_sidecar_sync(
            "11111111-1111-1111-1111-111111111111",
            sidecar_path=sidecar,
            workspace_root=sidecar.parents[3],
            kind="generated",
        )

    asyncio.run(caller())

    assert "upsert" in calls, "dual-write skipped when called from a running loop"


def test_dual_write_still_fires_from_a_sync_caller(monkeypatch, sidecar: Path) -> None:
    """The path that always worked must keep working."""
    calls: list[str] = []
    _install(monkeypatch, calls)

    catalog_index.index_sidecar_sync(
        "11111111-1111-1111-1111-111111111111",
        sidecar_path=sidecar,
        workspace_root=sidecar.parents[3],
        kind="generated",
    )

    assert "upsert" in calls


def test_dual_write_failure_is_reported_at_warning(monkeypatch, sidecar: Path, caplog) -> None:
    """Swallowed, but never into the dark: a failure has to be visible in logs."""
    import brand_ops.db.base as db_base

    def _boom():
        raise RuntimeError("no DATABASE_URL")

    monkeypatch.setattr(db_base, "get_bridge_sessionmaker", _boom, raising=False)

    async def caller() -> None:
        catalog_index.index_sidecar_sync(
            "11111111-1111-1111-1111-111111111111",
            sidecar_path=sidecar,
            workspace_root=sidecar.parents[3],
            kind="generated",
        )

    with caplog.at_level("WARNING", logger="brand_ops.catalog_index"):
        asyncio.run(caller())  # must not raise: the caller's bytes already landed

    assert any("dual-write" in r.message or "dual-write" in r.getMessage() for r in caplog.records)


def test_no_workspace_is_still_a_no_op(monkeypatch, sidecar: Path) -> None:
    """CLI / stdio MCP / AUTH-off have no tenant to index against."""
    calls: list[str] = []
    _install(monkeypatch, calls)

    catalog_index.index_sidecar_sync(
        None,
        sidecar_path=sidecar,
        workspace_root=sidecar.parents[3],
        kind="generated",
    )

    assert calls == []
