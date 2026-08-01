"""Index an on-disk sidecar into the Postgres catalog: the one writer both the
dual-write and the backfill share.

Every generated or uploaded asset already drops a ``<file>.json`` sidecar (see
``output.save_result`` / ``ingest._write_sidecar``). This module turns one such
sidecar into one ``asset`` row, keyed by the file's workspace-relative
``storage_key`` so the write is idempotent (``repository.upsert_asset``). The same
function is called two ways:

  * the dual-write -- ``index_sidecar_sync`` -- runs best-effort right after a new
    generation/upload writes its sidecar, so the DB tracks new assets going
    forward. It mirrors the cost-metering bridge: a no-op without an active
    workspace or a ``DATABASE_URL``, and a DB failure is swallowed so a metering
    or indexing problem can never break a generation whose bytes already landed.
  * the backfill -- ``catalog_backfill`` -- walks every existing sidecar and calls
    the async core under each workspace's scope, so the DB catches up on history.

Reads stay on the sidecars (``api/catalog.read_assets``) until an operator flips
``BRAND_OPS_CATALOG_SOT=db``; this module only populates the rows.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger("brand_ops.catalog_index")

# How long an async caller will wait for the dual-write worker thread. One upsert
# is milliseconds; this is only a guard so a wedged DB cannot pin a request.
_DUAL_WRITE_TIMEOUT_S = 15.0

# The two on-disk catalog subtrees, which are also the AssetRecord ``kind`` values.
STORAGE_KINDS: tuple[str, ...] = ("generated", "imported")


def sidecar_fields(data: dict[str, Any]) -> dict[str, Any] | None:
    """Map a sidecar dict to ``upsert_asset`` keyword fields, or None if not one.

    Returns None for anything that is not an asset sidecar (no ``asset_type``),
    matching the catalog reader's own guard, so a stray JSON file is skipped rather
    than indexed. ``size`` ([w, h]) is split into width/height; ``created_at`` (the
    sidecar's string label, e.g. ``2026-06-20-14-30-00``) becomes ``created_label``
    so a DB read sorts identically to a sidecar read. ``kind`` is NOT taken from the
    sidecar (the reader derives it from the directory, generated/ vs imported/), so
    the caller passes it; brand/storage_key are the caller's too.
    """
    if not isinstance(data, dict) or "asset_type" not in data:
        return None
    size = data.get("size")
    width = height = None
    if isinstance(size, (list, tuple)) and len(size) >= 2:
        try:
            width, height = int(size[0]), int(size[1])
        except (TypeError, ValueError):
            width = height = None
    refs = data.get("reference_images")
    ref_list = [str(r) for r in refs] if isinstance(refs, list) else None
    raw_status = data.get("lifecycle_status")
    lifecycle_status = raw_status if isinstance(raw_status, str) else None
    raw_compliance = data.get("compliance")
    compliance = raw_compliance if isinstance(raw_compliance, dict) else None
    transparent = data.get("transparent")
    created_label = data.get("created_at")
    return {
        "asset_type": str(data.get("asset_type", "")),
        "variant": data.get("variant"),
        "topic": data.get("topic"),
        "platform": data.get("platform"),
        "backend": data.get("backend"),
        "model": data.get("model"),
        "mime": data.get("mime"),
        "width": width,
        "height": height,
        "transparent": transparent if isinstance(transparent, bool) else None,
        "cost_cents": data.get("cost_cents"),
        "prompt": data.get("prompt"),
        "reference_images": ref_list,
        "lifecycle_status": lifecycle_status,
        "compliance": compliance,
        "created_label": created_label if isinstance(created_label, str) else None,
    }


def storage_key_for(image_path: Path, workspace_root: Path) -> str | None:
    """The file's path relative to its workspace root, as a forward-slash key.

    The key the catalog rows are unique on: ``<slug>/<kind>/<asset_type>/<file>``.
    Returns None when the image is not under the workspace root (so a stray path
    can never be keyed against the wrong tenant), using ``is_relative_to`` rather
    than catching ``relative_to``'s exception so the intent is explicit.
    """
    try:
        root = workspace_root.resolve()
        target = image_path.resolve()
    except OSError:  # pragma: no cover - resolve() rarely raises on these inputs
        return None
    if target == root or not target.is_relative_to(root):
        return None
    return target.relative_to(root).as_posix()


def image_for_sidecar(sidecar_path: Path) -> Path:
    """The image a ``<image>.<ext>.json`` sidecar describes (drop the .json)."""
    return sidecar_path.with_suffix("")


async def upsert_sidecar(
    session: Any,
    workspace_id: str,
    *,
    sidecar_path: Path,
    workspace_root: Path,
    kind: str,
    created_by: str | None = None,
) -> bool:
    """Index one sidecar into the catalog under the caller's transaction.

    Reads the sidecar, derives the brand slug + storage key from the path, and
    upserts the ``asset`` row. Returns True when a row was written, False when the
    file is unreadable, is not an asset sidecar, or sits outside the workspace root
    (no clean key). The caller owns the scope + transaction: pass a session already
    inside ``workspace_session(workspace_id)`` so the INSERT/UPDATE satisfies RLS.
    """
    from .db.repository import upsert_asset

    try:
        data = json.loads(sidecar_path.read_text())
    except (OSError, ValueError):
        return False
    fields = sidecar_fields(data)
    if fields is None:
        return False

    image_path = image_for_sidecar(sidecar_path)
    storage_key = storage_key_for(image_path, workspace_root)
    if storage_key is None:
        return False
    # The brand slug is the first path segment of the key
    # (<slug>/<kind>/<asset_type>/<file>); fall back to the sidecar's recorded
    # brand if the layout is unexpected.
    parts = storage_key.split("/", 1)
    brand_slug = parts[0] if parts and parts[0] else str(data.get("brand", ""))
    if not brand_slug or not fields["asset_type"]:
        return False

    await upsert_asset(
        session,
        workspace_id,
        storage_key=storage_key,
        brand_slug=brand_slug,
        kind=kind,
        created_by=created_by,
        **fields,
    )
    return True


def index_sidecar_sync(
    workspace_id: str | None,
    *,
    sidecar_path: Path,
    workspace_root: Path,
    kind: str,
    created_by: str | None = None,
) -> None:
    """Best-effort dual-write: index one just-written sidecar into the catalog.

    Called from the generation/upload chokepoints after the sidecar is written. A
    complete no-op when there is no active workspace (the CLI / stdio MCP / tests
    / AUTH off, where there is no tenant to index against) or no ``DATABASE_URL``.
    A failure -- no DB, an RLS or transport error, an unreadable sidecar -- is
    swallowed: a broken catalog index is never allowed to undo a generation or
    upload whose bytes already landed, exactly the posture of the cost-metering
    bridge next to it.

    It is swallowed LOUDLY, at warning. Once ``BRAND_OPS_CATALOG_SOT=db`` the
    index is not a bonus, it is what the Library reads: an asset that misses it
    has landed on disk and is invisible in the product. This previously logged at
    debug, and when the async callers below stopped reaching the write the whole
    catalog silently stopped growing for three weeks with nothing in the logs.
    """
    if not workspace_id:
        return
    import asyncio

    async def _run() -> None:
        # The NullPool bridge sessionmaker: this runs on a throwaway asyncio.run
        # loop in a threadpool worker, so it must not reuse the pooled engine's
        # loop-bound connections (see db.base.get_bridge_sessionmaker).
        from .db.base import get_bridge_sessionmaker, workspace_session

        sessionmaker = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
        async with sessionmaker() as session:
            async with workspace_session(session, workspace_id) as scoped:
                await upsert_sidecar(
                    scoped,
                    workspace_id,
                    sidecar_path=sidecar_path,
                    workspace_root=workspace_root,
                    kind=kind,
                    created_by=created_by,
                )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # The plain path: a sync chokepoint with no running loop (CLI, or a sync
        # route FastAPI already offloaded to a threadpool worker).
        try:
            asyncio.run(_run())
        except Exception:  # noqa: BLE001 - best-effort; never fatal to the caller
            _log.warning("catalog dual-write FAILED for %s", sidecar_path, exc_info=True)
        return

    # An async caller (the REST routes and the HTTP MCP server both run their
    # handlers on the event loop). asyncio.run cannot drive a second loop on this
    # thread, so run the index on its own loop in a worker thread and wait for it.
    # This used to just return, which meant every asset created through the async
    # surfaces skipped the catalog entirely -- the bytes landed, the sidecar
    # landed, and the Library never showed it. The bridge sessionmaker is NullPool
    # precisely so it can be driven from a throwaway loop like this one.
    failure: list[BaseException] = []

    def _worker() -> None:
        try:
            asyncio.run(_run())
        except BaseException as exc:  # noqa: BLE001 - reported, never re-raised here
            failure.append(exc)

    import threading

    thread = threading.Thread(target=_worker, name="catalog-dual-write", daemon=True)
    thread.start()
    thread.join(timeout=_DUAL_WRITE_TIMEOUT_S)
    if thread.is_alive():
        _log.warning(
            "catalog dual-write TIMED OUT after %ss for %s", _DUAL_WRITE_TIMEOUT_S, sidecar_path
        )
    elif failure:
        _log.warning(
            "catalog dual-write FAILED for %s", sidecar_path, exc_info=failure[0]
        )
