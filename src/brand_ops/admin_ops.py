"""On-disk helpers for tenant offboarding and data export (admin only).

Two filesystem operations sit behind the admin API (api/admin.py): purging a
workspace's brand-kits subtree when its DB row is deleted, and building a zip
export of that subtree plus a manifest. Both are confined to the per-workspace
tree ``brand-kits/<workspace_id>/`` and refuse to act on a path that resolves
outside the kits root, so neither can ever touch another tenant's files (defense
in depth beneath the route's admin gate). The path-guard and the rmtree/copy
shape follow ``migrate_storage`` and the ``brand_loader`` read boundary.

The export is the inverse of an offboard, so it covers BOTH halves of what
``delete_workspace`` erases: the on-disk subtree AND the DB-only rows. This
module owns only the filesystem half; the route (which holds the DB session)
reads the tenant's rows under its own row level security and hands them in as a
plain ``db_rows`` mapping, which the archive writes under a ``db/`` subtree (one
JSON file per table) with the per-table row counts recorded in the manifest. That
keeps this module free of any DB session coupling: it never opens a connection,
it just serializes a mapping the caller already produced.

These never read or set the request-scoped brand_loader scope: they take an
explicit workspace id and resolve the on-disk root from it, so they are safe to
call from the admin path regardless of which workspace (if any) the request is
otherwise scoped to.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .brand_loader import DEFAULT_KITS_DIR

# The zip member prefix and the manifest filename. The manifest is written at the
# top of the archive so a consumer can read it first without scanning the tree.
_MANIFEST_NAME = "manifest.json"

# The archive subtree for the DB-only export: one ``db/<table>.json`` member per
# table, holding the tenant's rows for the tables delete_workspace would CASCADE.
# Kept under its own prefix so it never collides with a brand named "db" on disk
# (a brand subtree is stored at the archive root, this is one level in under db/).
_DB_PREFIX = "db"


def _kits_root() -> Path:
    """The resolved brand-kits root the per-workspace subtrees live under.

    Read from ``brand_loader.DEFAULT_KITS_DIR`` at call time (not import time) so
    a test that monkeypatches the kits dir is honored. Resolved so the
    ``is_relative_to`` containment check below compares two absolute paths.
    """
    return DEFAULT_KITS_DIR.resolve()


def _workspace_dir(workspace_id: str, *, root: Path | None = None) -> Path:
    """The resolved on-disk subtree for a workspace, guarded against escape.

    ``workspace_id`` is parsed as a uuid first, so it can only ever be one safe
    path segment: a value carrying a separator, a ``..``, or an absolute marker
    raises ``ValueError`` before it is ever joined onto the root, which closes
    path traversal here the same way ``brand_loader`` closes it for brand slugs.
    The joined path is then resolved and asserted to live under the kits root, so
    even a pathological-but-parseable input cannot point the purge/export at
    another directory.
    """
    canonical = str(uuid.UUID(str(workspace_id)))
    base = (root or _kits_root()).resolve()
    target = (base / canonical).resolve()
    if target != base and not target.is_relative_to(base):
        # Unreachable for a canonical uuid (it has no separators), but asserted
        # so a future change to the id shape cannot silently open an escape.
        raise ValueError("workspace path escapes the kits root")
    return target


def _brand_slugs_on_disk(ws_dir: Path) -> list[str]:
    """The brand slugs physically present in a workspace subtree, sorted.

    A brand is a child directory that holds a ``brand.yaml`` and is not an
    infrastructure entry (``_``- or dot-prefixed), matching how the file loader
    enumerates brands. No brand is loaded or validated here: this is a directory
    listing for the export manifest and the purge summary, so a malformed
    brand.yaml still counts as a present brand.
    """
    if not ws_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in ws_dir.iterdir()
        if p.is_dir()
        and not p.name.startswith(("_", "."))
        and (p / "brand.yaml").exists()
    )


def _count_files(path: Path) -> int:
    """Total files (not dirs) under ``path`` recursively. 0 when path is absent."""
    if not path.is_dir():
        return 0
    return sum(1 for p in path.rglob("*") if p.is_file())


def workspace_disk_summary(
    workspace_id: str, *, root: Path | None = None
) -> dict[str, Any]:
    """Describe a workspace's on-disk footprint without changing anything.

    Returns ``{exists, path, brands, file_count}`` for the subtree at
    ``brand-kits/<workspace_id>/``: whether it is present, its (string) path, the
    brand slugs in it, and a recursive file count. Used to build the delete
    summary and to populate the export manifest. Read-only.
    """
    ws_dir = _workspace_dir(workspace_id, root=root)
    brands = _brand_slugs_on_disk(ws_dir)
    return {
        "exists": ws_dir.is_dir(),
        "path": str(ws_dir),
        "brands": brands,
        "file_count": _count_files(ws_dir),
    }


def purge_workspace_tree(
    workspace_id: str, *, root: Path | None = None
) -> dict[str, Any]:
    """Remove a workspace's brand-kits subtree. Idempotent.

    Deletes ``brand-kits/<workspace_id>/`` and everything under it after the
    containment guard in ``_workspace_dir`` has proven the path is inside the
    kits root. Returns ``{removed, path, brands, file_count}`` describing what was
    there before the delete (``removed`` is False when the subtree did not exist,
    so a second purge is a safe no-op). DELIBERATE destructive operation, called
    only from the admin offboarding route after the DB row (and its CASCADE) is
    already gone.
    """
    summary = workspace_disk_summary(workspace_id, root=root)
    ws_dir = Path(summary["path"])
    if summary["exists"]:
        shutil.rmtree(ws_dir)
    return {
        "removed": summary["exists"],
        "path": summary["path"],
        "brands": summary["brands"],
        "file_count": summary["file_count"],
    }


def build_manifest(
    workspace_id: str,
    *,
    slug: str | None = None,
    root: Path | None = None,
    db_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The export manifest for a workspace: id, slug, brands, counts, placeholder.

    ``exported_at`` is left null (a placeholder) so this stays clock-free and the
    same inputs always yield the same manifest; the route may stamp the real wall
    time onto the response. ``slug`` is the workspace slug when the caller knows
    it (from the DB), else null. Brand and file counts come from the on-disk
    subtree. ``db_rows`` (the route's per-table dump, table name to list of rows)
    contributes a ``db_row_counts`` map of table name to row count and a
    ``db_row_total``, so the manifest records exactly how much DB-only data the
    archive carries; omitted (no DB configured) leaves both at their empty
    defaults rather than inventing counts.
    """
    summary = workspace_disk_summary(workspace_id, root=root)
    db_counts = {table: len(rows) for table, rows in (db_rows or {}).items()}
    return {
        "workspace_id": str(uuid.UUID(str(workspace_id))),
        "slug": slug,
        "brands": summary["brands"],
        "brand_count": len(summary["brands"]),
        "file_count": summary["file_count"],
        "db_row_counts": db_counts,
        "db_row_total": sum(db_counts.values()),
        "exported_at": None,
    }


def build_export_zip(
    workspace_id: str,
    *,
    slug: str | None = None,
    root: Path | None = None,
    db_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    """Write a zip of a workspace's subtree (+ manifest + DB dump); return it.

    The archive contains ``manifest.json`` at its top, then every file under
    ``brand-kits/<workspace_id>/`` (brand.yaml, asset sidecars, generated/ and
    imported/ trees) stored under its workspace-relative path, then (when
    ``db_rows`` is supplied) one ``db/<table>.json`` member per table holding that
    table's rows. ``db_rows`` is the route's per-table dump, read from the DB
    under the tenant's own row level security and passed in here so this module
    never touches a DB session; it makes the export the inverse of
    ``delete_workspace`` (which erases those same DB rows on offboard). An empty or
    absent subtree, and an omitted or empty ``db_rows``, still yields a valid zip:
    the manifest is always present and each ``db/<table>.json`` is written even
    when its row list is empty (an empty array), so a consumer sees every table.
    The caller owns the returned temp file and cleans it up after streaming. Read
    only with respect to the kits tree and the database.
    """
    ws_dir = _workspace_dir(workspace_id, root=root)
    manifest = build_manifest(workspace_id, slug=slug, root=root, db_rows=db_rows)

    # delete=False: the file must outlive this function so the route can stream
    # it; the route deletes it once the response is sent.
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"brand-ops-export-{manifest['workspace_id']}-",
        suffix=".zip",
        delete=False,
    )
    tmp.close()
    out = Path(tmp.name)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(_MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
        if ws_dir.is_dir():
            for file_path in sorted(ws_dir.rglob("*")):
                if file_path.is_file():
                    zf.write(file_path, arcname=file_path.relative_to(ws_dir).as_posix())
        # The DB-only half: one JSON file per table under db/. Written in sorted
        # table order so an unchanged workspace produces a byte-stable archive.
        for table in sorted(db_rows or {}):
            zf.writestr(
                f"{_DB_PREFIX}/{table}.json",
                json.dumps(db_rows[table], indent=2, sort_keys=True),
            )
    return out


def iter_and_cleanup(path: Path, *, chunk_size: int = 1 << 16) -> Iterator[bytes]:
    """Yield a file's bytes in chunks, then delete it. For a streaming response.

    Lets the export route hand a FileResponse-like generator back to the client
    and guarantee the temp zip is removed once the body is fully sent (including
    on a client disconnect, since the generator's finally still runs). The unlink
    is best effort: a missing file (already cleaned) is ignored.
    """
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        path.unlink(missing_ok=True)
