"""Soft-delete a catalog asset: the recoverable, sidecar-durable hide.

Until now Brand-Ops could not delete a single catalog asset, so removed data
lingered forever -- an enterprise-audit gap. The soft-delete is the recoverable
half of the fix: it marks an asset deleted (hidden from the catalog) without
destroying its bytes, and the retention sweeper (``brand_ops.retention_sweep``)
hard-purges what has aged past the configured window.

``soft_delete_sync`` runs the two writes the DELETE /assets/file route needs,
modeled on the catalog dual-write and ``idempotency.begin_sync`` beside it: it
executes on a throwaway ``asyncio.run`` loop inside the synchronous FastAPI
threadpool route, so it uses the NullPool bridge sessionmaker (never the pooled
engine's loop-bound connections).

It writes ``deleted_at`` two places, and the SIDECAR write is the durable hide:

  1. The on-disk ``<file>.json`` sidecar gets ``deleted_at`` set (atomically), so
     even with the DB flag off (``BRAND_OPS_CATALOG_SOT`` unset, the default) the
     file-walk read path hides the asset (``catalog._coerce_record`` skips a
     sidecar with a truthy ``deleted_at``). This is the source of truth for the
     hide and is done first.
  2. The ``asset`` row's ``deleted_at`` is stamped under the workspace scope, so
     the DB read path (``repository.list_assets``, filtered ``deleted_at IS
     NULL``) hides it too once the catalog SoR is Postgres.

The DB step is best-effort on the INFRASTRUCTURE boundary only (no active
workspace, no ``DATABASE_URL``, a running loop, or a transient error), degrading
the same way the metering and idempotency bridges do: the sidecar hide still
stands and the route still succeeds. The wall-clock ``deleted_at`` is the
caller's (the HTTP boundary stamps it), so this helper reads no clock and stays
reproducible, exactly as the activity feed and the lifecycle transition do.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .atomic_write import atomic_write_text

_log = logging.getLogger("brand_ops.retention")


def _running_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _stamp_sidecar(sidecar_path: Path, deleted_at: str) -> None:
    """Set ``deleted_at`` on the sidecar JSON in place, atomically. Best-effort.

    Reads the existing sidecar, sets ``data["deleted_at"] = deleted_at``,
    preserving every other key, and rewrites it via the crash-safe atomic writer.
    A missing or unreadable/non-object sidecar is left as-is (the asset may be a
    DB-only row, or the bytes may already be gone): the caller still proceeds to
    the DB update, so the hide is not blocked by a file the read path could not
    have surfaced anyway. Never raises into the caller.
    """
    try:
        data = json.loads(sidecar_path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    data["deleted_at"] = deleted_at
    try:
        atomic_write_text(sidecar_path, json.dumps(data, indent=2))
    except OSError:
        _log.warning("could not stamp deleted_at into sidecar %s", sidecar_path.name)


def soft_delete_sync(
    workspace_id: str | None,
    *,
    storage_key: str,
    sidecar_path: Path,
    deleted_at: str,
) -> bool:
    """Soft-delete one asset: stamp ``deleted_at`` into the sidecar, then the row.

    The sidecar write is the durable hide and runs first (so the file read path
    hides the asset regardless of the DB). The DB update is best-effort on the
    infrastructure boundary -- it degrades to a no-op (the sidecar hide still
    stands) when there is no workspace, a running loop prevents the sync bridge,
    no ``DATABASE_URL``, or any transient error -- mirroring the metering and
    idempotency bridges. ``deleted_at`` is the caller's ISO-8601 UTC string, so
    this helper reads no clock.

    Returns True when the DB row was tombstoned by this call (a live asset row
    existed and was hidden), False otherwise (no workspace / no DB / a row that
    was already soft-deleted or had no DB entry). The return reflects only the DB
    write; the sidecar hide is attempted unconditionally and is what the read
    paths rely on.
    """
    # The durable hide: always attempt the sidecar stamp, even with no DB scope.
    _stamp_sidecar(sidecar_path, deleted_at)

    if not workspace_id or _running_loop():
        return False
    import asyncio

    async def _run() -> bool:
        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.repository import soft_delete_asset

        sm = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
        async with sm() as session:
            async with workspace_session(session, workspace_id) as scoped:
                return await soft_delete_asset(scoped, workspace_id, storage_key=storage_key)

    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 - best-effort on the infra boundary
        _log.debug("soft-delete DB update skipped", exc_info=True)
        return False
