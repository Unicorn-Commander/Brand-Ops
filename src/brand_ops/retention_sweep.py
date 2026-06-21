"""Retention sweep: hard-purge soft-deleted assets past the retention window.

The destructive half of the data-retention fix. A soft-delete (DELETE
/api/assets/file -> ``retention.soft_delete_sync``) hides an asset but keeps its
bytes, so it stays recoverable. This sweep is what eventually removes the data
that has aged out, closing the enterprise-audit gap that deleted data lingered
forever.

DISABLED BY DEFAULT. ``BRAND_OPS_RETENTION_DAYS`` (env, default 0) is the window;
while it is unset or <= 0 the sweep purges NOTHING and exits cleanly, so an
operator never silently destroys data they did not opt into a window for. With a
positive value, an asset is purged only once its ``deleted_at`` tombstone is
older than ``now - days``.

Run manually (or from cron) on each node, after ``alembic upgrade head`` has
added ``asset.deleted_at`` (migration 0007):

    docker exec brand-ops-backend python -m brand_ops.retention_sweep
    docker exec brand-ops-backend python -m brand_ops.retention_sweep --dry-run

It walks each ``<workspace_id>/`` subtree under the kits root exactly as the
backfill does (a non-uuid top-level entry -- the shared ``_template`` -- is
skipped). For each workspace, under that tenant's RLS scope, it finds the rows
whose ``deleted_at`` predates the cutoff and, for each, deletes the on-disk file
and its ``.json`` sidecar (confined under the workspace root so a crafted
storage key cannot escape the tree) and then hard-deletes the row. Per-workspace
counts ({purged, skipped}) and a total are logged. ``--dry-run`` reports what
WOULD be purged and deletes nothing.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .brand_loader import safe_subpath
from .db.tenancy import coerce_uuid

_log = logging.getLogger("brand_ops.retention_sweep")


def retention_days() -> int:
    """The retention window in days from ``BRAND_OPS_RETENTION_DAYS`` (default 0).

    Read at call time, never cached, so a deploy can set it without a code change
    and a test can monkeypatch the env. A missing, blank, non-integer, or negative
    value reads as 0 -- the safe default that disables the purge (the sweep never
    destroys data unless an operator opts into a positive window).
    """
    raw = os.getenv("BRAND_OPS_RETENTION_DAYS", "").strip()
    try:
        days = int(raw)
    except ValueError:
        return 0
    return days if days > 0 else 0


def _purge_paths(ws_dir: Path, storage_key: str, *, dry_run: bool) -> bool:
    """Delete one asset's on-disk file + sidecar, confined under ``ws_dir``.

    ``storage_key`` is the catalog key ``<slug>/<kind>/<asset_type>/<file>``, so
    the file is ``ws_dir / storage_key``. Each segment is routed through
    ``safe_subpath`` (the shared traversal guard) so a crafted key cannot resolve
    outside the workspace subtree; an unsafe key is skipped (returns False, never
    a delete outside the tree). Removes the image and its ``<file>.json`` sidecar,
    each ``missing_ok`` (a half-present pair is fine). Returns True when the on-disk
    paths were confined and (in a real run) removed, so the caller may proceed to
    purge the row; ``--dry-run`` confines and reports but deletes nothing.
    """
    parts = [p for p in storage_key.split("/") if p]
    target = safe_subpath(ws_dir, *parts)
    if target is None:
        _log.warning("unsafe storage key %r under %s; skipping", storage_key, ws_dir)
        return False
    if dry_run:
        return True
    sidecar = target.with_suffix(target.suffix + ".json")
    target.unlink(missing_ok=True)
    sidecar.unlink(missing_ok=True)
    return True


async def sweep_workspace(
    ws_id: str, ws_dir: Path, cutoff: datetime, *, dry_run: bool
) -> dict[str, int]:
    """Purge one workspace's aged-out soft-deleted assets; returns {purged, skipped}.

    Opens a single transaction scoped to ``ws_id`` and, for every asset whose
    ``deleted_at`` predates ``cutoff``, deletes its on-disk file + sidecar (under
    the workspace root) and then hard-deletes the row, so the whole workspace's
    purge lands atomically and under RLS. An asset whose storage key is unsafe or
    absent (no key) is counted as skipped and left untouched. ``--dry-run`` counts
    what would be purged and writes nothing (it never opens a write transaction's
    commit; SQLAlchemy rolls the read-only scope back).
    """
    from .db.base import get_sessionmaker, workspace_session
    from .db.repository import list_soft_deleted_before, purge_asset

    sessionmaker = get_sessionmaker()
    purged = skipped = 0
    async with sessionmaker() as session:
        async with workspace_session(session, ws_id) as scoped:
            candidates = await list_soft_deleted_before(scoped, ws_id, cutoff)
            for asset in candidates:
                storage_key = asset.storage_key or ""
                if not storage_key:
                    # A keyless soft-deleted row has no file to remove; leave it
                    # rather than guess a path. Rare (pre-SoR rows), counted.
                    skipped += 1
                    continue
                if not _purge_paths(ws_dir, storage_key, dry_run=dry_run):
                    skipped += 1
                    continue
                # The on-disk file is removed best-effort ahead of the row purge. If
                # the row purge later fails (or the workspace transaction rolls back),
                # the bytes are gone but the row survives as a still-soft-deleted,
                # still-aged-out candidate -- so the next sweep re-purges it cleanly
                # (purge_paths is missing_ok). No data the operator wanted kept is at
                # risk: only assets already past the retention window are touched.
                if not dry_run:
                    await purge_asset(scoped, ws_id, asset.id)
                purged += 1
    return {"purged": purged, "skipped": skipped}


async def sweep(
    kits_dir: Path | None = None, *, dry_run: bool = False
) -> dict[str, dict[str, int]]:
    """Purge aged-out soft-deleted assets in every workspace. Returns per-ws counts.

    Computes ``cutoff = now - BRAND_OPS_RETENTION_DAYS`` and walks each
    ``<workspace_id>/`` subtree under the kits root (a non-uuid entry -- the shared
    ``_template`` -- is skipped). Each workspace is swept independently and wrapped
    so one failure is logged and the rest still run. Returns ``{workspace_id:
    {"purged": n, "skipped": m}}`` (skipped = -2 flags a workspace that raised).
    Returns ``{}`` without touching anything when retention is disabled (days <= 0),
    so a disabled sweep can never purge.
    """
    from .brand_loader import DEFAULT_KITS_DIR

    days = retention_days()
    if days <= 0:
        return {}

    cutoff = datetime.now(UTC) - timedelta(days=days)
    root = kits_dir or DEFAULT_KITS_DIR
    results: dict[str, dict[str, int]] = {}
    if not root.is_dir():
        return results
    for ws_dir in sorted(root.iterdir()):
        ws_id = coerce_uuid(ws_dir.name) if ws_dir.is_dir() else None
        if ws_id is None:
            continue
        try:
            results[ws_id] = await sweep_workspace(ws_id, ws_dir, cutoff, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one bad tenant never aborts the rest
            _log.error(
                "retention sweep failed for workspace %s: %s", ws_id, exc.__class__.__name__
            )
            results[ws_id] = {"purged": 0, "skipped": -2}
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hard-purge soft-deleted assets past the retention window."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be purged per workspace without deleting anything",
    )
    parser.add_argument(
        "--kits-dir",
        type=Path,
        default=None,
        help="override the brand-kits root (defaults to the configured volume)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    days = retention_days()
    if days <= 0:
        # The safe default: never purge unless an operator set a positive window.
        _log.info(
            "retention disabled (BRAND_OPS_RETENTION_DAYS unset/0); nothing purged"
        )
        return 0

    results = asyncio.run(sweep(args.kits_dir, dry_run=args.dry_run))

    total_purged = sum(r["purged"] for r in results.values())
    verb = "would purge" if args.dry_run else "purged"
    for ws_id, counts in sorted(results.items()):
        note = ""
        if counts["skipped"] == -2:
            note = " (FAILED)"
        elif counts["skipped"] > 0:
            note = f" ({counts['skipped']} unkeyable/unsafe skipped)"
        print(f"  {ws_id}: {verb} {counts['purged']}{note}")
    print(
        f"{verb} {total_purged} asset(s) across {len(results)} workspace(s) "
        f"(retention {days}d)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
