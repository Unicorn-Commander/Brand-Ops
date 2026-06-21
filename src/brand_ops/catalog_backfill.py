"""One-time backfill: index every existing on-disk sidecar into the Postgres
catalog, so the DB catches up on the assets that predate the dual-write.

Run manually on each node AFTER ``alembic upgrade head`` brings the catalog
columns + the unique key in (migration 0004), and after the dual-write is
deployed so new assets are already landing in the DB:

    python -m brand_ops.catalog_backfill            # index every workspace
    python -m brand_ops.catalog_backfill --dry-run  # count, write nothing

It walks ``brand-kits/<workspace_id>/<slug>/{generated,imported}/`` exactly as the
catalog reader does, and upserts one ``asset`` row per sidecar under that
workspace's RLS scope. Idempotent: the row is keyed on ``(workspace_id,
storage_key)``, so re-running only refreshes. A workspace with no anchor row in
the ``workspace`` table is skipped (its assets have nowhere to foreign-key to);
every other failure is caught per workspace so one bad tenant cannot abort the
rest. Reads stay on the sidecars until an operator flips ``BRAND_OPS_CATALOG_SOT``;
this only populates the rows, so it is safe to run on a live node.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .catalog_index import STORAGE_KINDS, sidecar_fields, storage_key_for, upsert_sidecar
from .db.tenancy import coerce_uuid

_log = logging.getLogger("brand_ops.catalog_backfill")


def _iter_workspace_sidecars(ws_dir: Path) -> list[tuple[Path, str]]:
    """Every (sidecar_path, kind) under one workspace's brand subtrees.

    Walks ``<ws>/<slug>/{generated,imported}/`` and yields each ``*.json`` paired
    with its kind (the subtree it was found under), the same shape the catalog
    reader derives ``AssetRecord.kind`` from. Brand and asset-type dirs are
    discovered by the walk, so a new asset type needs no change here.
    """
    found: list[tuple[Path, str]] = []
    if not ws_dir.is_dir():
        return found
    for slug_dir in sorted(ws_dir.iterdir()):
        if not slug_dir.is_dir() or slug_dir.name.startswith("_"):
            continue
        for kind in STORAGE_KINDS:
            kind_dir = slug_dir / kind
            if not kind_dir.is_dir():
                continue
            for sidecar in sorted(kind_dir.rglob("*.json")):
                found.append((sidecar, kind))
    return found


async def _count_indexable(ws_dir: Path) -> int:
    """How many sidecars under a workspace are real, keyable asset records.

    The dry-run counterpart to the write: mirrors ``upsert_sidecar``'s accept/skip
    logic (a readable asset sidecar that resolves to a clean storage key) without
    touching the DB, so an operator can preview the backfill volume.
    """
    n = 0
    for sidecar, _kind in _iter_workspace_sidecars(ws_dir):
        try:
            data = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        if sidecar_fields(data) is None:
            continue
        if storage_key_for(sidecar.with_suffix(""), ws_dir) is None:
            continue
        n += 1
    return n


async def backfill_workspace(ws_id: str, ws_dir: Path, *, dry_run: bool) -> dict[str, int]:
    """Index one workspace's sidecars; returns {indexed, skipped} counts.

    Opens a single transaction scoped to ``ws_id`` and upserts every sidecar under
    it, so the whole workspace lands atomically and under RLS. Skips entirely (and
    reports it) when the workspace has no anchor row, since an asset insert would
    fail its foreign key. Dry-run only counts.
    """
    from .db.base import get_sessionmaker, workspace_session
    from .db.repository import get_workspace

    if dry_run:
        return {"indexed": await _count_indexable(ws_dir), "skipped": 0}

    sessionmaker = get_sessionmaker()
    indexed = skipped = 0
    async with sessionmaker() as session:
        # One transaction scoped to the workspace. The anchor-row check runs INSIDE
        # it (reading the non-RLS workspace table is scope-agnostic) so it does not
        # autobegin a separate transaction that would then collide with
        # workspace_session's own begin(). The asset FK targets workspace.id, so a
        # missing anchor would fail every insert; skip the whole workspace cleanly.
        async with workspace_session(session, ws_id) as scoped:
            if await get_workspace(scoped, ws_id) is None:
                _log.warning(
                    "workspace %s has no anchor row; skipping its assets", ws_id
                )
                return {"indexed": 0, "skipped": -1}
            for sidecar, kind in _iter_workspace_sidecars(ws_dir):
                ok = await upsert_sidecar(
                    scoped,
                    ws_id,
                    sidecar_path=sidecar,
                    workspace_root=ws_dir,
                    kind=kind,
                )
                if ok:
                    indexed += 1
                else:
                    skipped += 1
    return {"indexed": indexed, "skipped": skipped}


async def backfill(
    kits_dir: Path | None = None, *, dry_run: bool = False
) -> dict[str, dict[str, int]]:
    """Index every workspace's sidecars into the catalog. Returns per-ws counts.

    Walks each ``<workspace_id>/`` subtree under the kits root (a top-level entry
    that is not a uuid -- the shared ``_template`` -- is skipped). Each workspace is
    indexed independently and wrapped so one failure is logged and the rest still
    run. The returned mapping is ``{workspace_id: {"indexed": n, "skipped": m}}``
    (skipped = -1 flags a workspace with no anchor row).
    """
    from .brand_loader import DEFAULT_KITS_DIR

    root = kits_dir or DEFAULT_KITS_DIR
    results: dict[str, dict[str, int]] = {}
    if not root.is_dir():
        return results
    for ws_dir in sorted(root.iterdir()):
        ws_id = coerce_uuid(ws_dir.name) if ws_dir.is_dir() else None
        if ws_id is None:
            continue
        try:
            results[ws_id] = await backfill_workspace(ws_id, ws_dir, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - one bad tenant never aborts the rest
            _log.error("backfill failed for workspace %s: %s", ws_id, exc.__class__.__name__)
            results[ws_id] = {"indexed": 0, "skipped": -2}
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill the Postgres asset catalog from on-disk sidecars."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="count indexable sidecars per workspace without writing anything",
    )
    parser.add_argument(
        "--kits-dir",
        type=Path,
        default=None,
        help="override the brand-kits root (defaults to the configured volume)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    results = asyncio.run(backfill(args.kits_dir, dry_run=args.dry_run))

    total_indexed = sum(r["indexed"] for r in results.values())
    verb = "would index" if args.dry_run else "indexed"
    for ws_id, counts in sorted(results.items()):
        note = ""
        if counts["skipped"] == -1:
            note = " (NO ANCHOR ROW; skipped)"
        elif counts["skipped"] == -2:
            note = " (FAILED)"
        elif counts["skipped"] > 0:
            note = f" ({counts['skipped']} non-asset/unkeyable skipped)"
        print(f"  {ws_id}: {verb} {counts['indexed']}{note}")
    print(f"{verb} {total_indexed} asset(s) across {len(results)} workspace(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
