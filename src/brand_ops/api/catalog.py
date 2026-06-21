"""Read the on-disk asset catalog: the sidecar JSON records the output manager
writes next to every generated file.

The output manager (``brand_ops.output.save_result``) drops a ``<file>.json``
beside each generated image with the brand, asset type, backend, model, size,
cost, prompt, and references. This module walks those sidecars under
``brand-kits/<slug>/generated/<asset>/`` so the REST API can list what has been
made without a database. WS1 will later ingest the identical fields into
Postgres; until then the sidecars are the catalog.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlencode

from ..brand_loader import (
    current_kits_dir,
    get_allowed_brands,
    get_current_workspace_id,
    safe_subpath,
    workspace_query_suffix,
)
from .schemas import AssetRecord

_log = logging.getLogger("brand_ops.catalog")


def catalog_sot_enabled() -> bool:
    """True when the catalog source of truth is Postgres (``BRAND_OPS_CATALOG_SOT=db``).

    Default ``sidecar`` (any value but ``db``), so reads stay on the on-disk
    sidecars until an operator flips the flag -- the safe transition default. Read
    at call time, not cached, so a deploy can flip it without a restart and a test
    can monkeypatch the env.
    """
    return os.getenv("BRAND_OPS_CATALOG_SOT", "sidecar").strip().lower() == "db"


def _coerce_record(
    data: dict,
    sidecar: Path,
    image_path: Path,
    kind: str = "generated",
    brand: str | None = None,
) -> AssetRecord | None:
    """Build an AssetRecord from a sidecar dict. None if it is not a sidecar.

    ``brand`` is the asset's OWNING brand -- the directory the file physically
    lives under -- and when given it is authoritative over the sidecar's own
    ``brand`` field. They diverge for an imported/cloned asset whose sidecar
    records the SOURCE brand it came from while the file sits in the destination
    brand's kit: the file URL must resolve to where the bytes actually are, so the
    directory wins (a field-based URL would 404). Callers that know the directory
    (the catalog walk, the upload writer) pass it; ``brand=None`` falls back to the
    sidecar field for backward compatibility.
    """
    if not isinstance(data, dict) or "asset_type" not in data:
        return None
    size = data.get("size")
    size_list = [int(x) for x in size] if isinstance(size, list) else []
    refs = data.get("reference_images")
    ref_list = [str(r) for r in refs] if isinstance(refs, list) else []
    brand = brand if brand is not None else str(data.get("brand", ""))
    asset_type = str(data.get("asset_type", ""))
    file_name = image_path.name
    url = None
    if brand and asset_type and file_name:
        url = (
            "/api/assets/file?"
            + urlencode(
                {"brand": brand, "asset": asset_type, "file": file_name, "kind": kind}
            )
            + workspace_query_suffix()
        )
    # Lifecycle fields are surfaced verbatim from the sidecar and left null when
    # absent: a record that was never stamped reads as lifecycle_status=None, so
    # the backfill — not this read — is what populates them. compliance is the
    # small {status, score, violations} summary stamp_compliance writes.
    raw_status = data.get("lifecycle_status")
    lifecycle_status = raw_status if isinstance(raw_status, str) else None
    raw_compliance = data.get("compliance")
    compliance = raw_compliance if isinstance(raw_compliance, dict) else None
    return AssetRecord(
        brand=brand,
        asset_type=asset_type,
        variant=data.get("variant"),
        backend=data.get("backend"),
        model=data.get("model"),
        mime=data.get("mime"),
        size=size_list,
        transparent=data.get("transparent"),
        cost_cents=data.get("cost_cents"),
        created_at=data.get("created_at"),
        file=file_name,
        kind=kind,
        url=url,
        prompt=data.get("prompt"),
        reference_images=ref_list,
        lifecycle_status=lifecycle_status,
        compliance=compliance,
    )


def _iter_sidecars(root: Path) -> list[tuple[Path, Path]]:
    """Yield (sidecar_json, image_path) pairs under a generated directory."""
    pairs: list[tuple[Path, Path]] = []
    if not root.exists():
        return pairs
    for sidecar in sorted(root.rglob("*.json")):
        # The image is the sidecar minus the trailing .json (output.py writes
        # ``<image>.png.json``). If that file is missing we still surface the
        # record, since the JSON is the source of truth for the catalog.
        image_path = sidecar.with_suffix("")
        pairs.append((sidecar, image_path))
    return pairs


_KINDS = ("generated", "imported")


def _asset_to_record(asset) -> AssetRecord | None:
    """Build an AssetRecord from a Postgres ``asset`` row (the DB read path).

    The mirror of ``_coerce_record`` for a DB row instead of a sidecar dict: it
    reconstructs the exact same shape the sidecar path returns, including the
    ``/api/assets/file`` URL (derived from brand + asset type + the file name in
    ``storage_key``) and the workspace query suffix. A row without a storage key
    predates the source-of-truth transition and has no file to serve, so it is
    skipped (None).
    """
    storage_key = asset.storage_key or ""
    file_name = Path(storage_key).name
    if not file_name:
        return None
    brand = asset.brand_slug or ""
    asset_type = asset.asset_type or ""
    kind = asset.kind or "generated"
    url = None
    if brand and asset_type and file_name:
        url = (
            "/api/assets/file?"
            + urlencode(
                {"brand": brand, "asset": asset_type, "file": file_name, "kind": kind}
            )
            + workspace_query_suffix()
        )
    size: list[int] = []
    if asset.width is not None and asset.height is not None:
        size = [int(asset.width), int(asset.height)]
    cost = float(asset.cost_cents) if asset.cost_cents is not None else None
    refs = asset.reference_images if isinstance(asset.reference_images, list) else []
    compliance = asset.compliance if isinstance(asset.compliance, dict) else None
    return AssetRecord(
        brand=brand,
        asset_type=asset_type,
        variant=asset.variant,
        backend=asset.backend,
        model=asset.model,
        mime=asset.mime,
        size=size,
        transparent=asset.transparent,
        cost_cents=cost,
        created_at=asset.created_label or "",
        file=file_name,
        kind=kind,
        url=url,
        prompt=asset.prompt,
        reference_images=[str(r) for r in refs],
        lifecycle_status=asset.lifecycle_status,
        compliance=compliance,
    )


def _read_assets_db(
    brand: str | None, asset: str | None, status: str | None
) -> list[AssetRecord] | None:
    """Serve the catalog from Postgres under the request's workspace scope.

    Returns the records (possibly empty, which is a real "no assets" answer) on
    success, or None to signal "fall back to the sidecars" -- when there is no
    active workspace (the unscoped CLI / stdio MCP / tests, whose historical read
    is the file walk), when a running event loop prevents the sync bridge, or when
    the DB is unreachable. Reads are deliberately soft: the sidecars remain on disk
    as a derived artifact, so a metering-store-style outage degrades reads to the
    files rather than failing them. The brand allow-list and the status filter are
    applied exactly as the sidecar path applies them, so DB and file reads return
    the identical set.
    """
    ws = get_current_workspace_id()
    if not ws:
        return None  # unscoped: the file walk is the correct historical behavior
    allowed = get_allowed_brands()
    if brand and allowed is not None and brand not in allowed:
        return []  # a brand outside the workspace: empty, same as the sidecar gate

    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # the expected path: a sync route/MCP tool, no running loop
    else:
        return None  # inside a running loop: cannot bridge, use the sidecars

    async def _query():
        # NullPool bridge sessionmaker: read runs on a throwaway asyncio.run loop in
        # a threadpool worker, so it must not reuse the pooled engine's loop-bound
        # connections (see db.base.get_bridge_sessionmaker).
        from ..db import repository as repo
        from ..db.base import get_bridge_sessionmaker, workspace_session

        sm = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
        async with sm() as session:
            async with workspace_session(session, ws) as scoped:
                return list(await repo.list_assets(scoped, ws, brand, asset))

    try:
        rows = asyncio.run(_query())
    except Exception:  # noqa: BLE001 - reads are soft; degrade to the sidecars
        _log.debug("catalog DB read failed; falling back to sidecars", exc_info=True)
        return None

    records: list[AssetRecord] = []
    for row in rows:
        record = _asset_to_record(row)
        if record is None:
            continue
        if allowed is not None and not brand and record.brand not in allowed:
            continue
        if status is not None and record.lifecycle_status != status:
            continue
        records.append(record)
    # Newest first by the sidecar's string label, identical to the file path's sort.
    records.sort(key=lambda r: r.created_at or "", reverse=True)
    return records


def read_assets(
    brand: str | None = None,
    asset: str | None = None,
    kits_dir: Path | None = None,
    status: str | None = None,
) -> list[AssetRecord]:
    """Read catalog records, optionally filtered by brand, asset type, and status.

    When the source of truth is Postgres (``BRAND_OPS_CATALOG_SOT=db``) and this is
    the default-volume request path (no explicit ``kits_dir``), the records come
    from the DB under the request's workspace scope; any miss (unscoped, no DB, a
    running loop) falls back to the file walk below, so the sidecars stay a live
    standby. With the flag off (the default), or for an explicit ``kits_dir``
    (tests, the lifecycle sweep), the file walk is the only path.

    Walks both the generated and imported trees under each brand kit. Reads only
    the directories implied by the filters, and skips unreadable or non-sidecar
    JSON files rather than raising, so one bad file cannot break a list.

    ``status`` filters by lifecycle status ("approved"/"needs_review"): a record
    whose ``lifecycle_status`` differs (including a never-stamped null) is
    dropped. Absent, every record is returned regardless of lifecycle status.
    """
    if kits_dir is None and catalog_sot_enabled():
        records = _read_assets_db(brand, asset, status)
        if records is not None:
            return records

    root = kits_dir or current_kits_dir()
    if not root.exists():
        return []

    # Tenant gate: the same workspace allow-list the brand loader uses. When the
    # request is scoped (allowed is not None) only visible brands are scanned, so
    # the no-brand-filter listing cannot leak another workspace's assets and a
    # request for a brand outside the workspace returns nothing. Unscoped (CLI /
    # stdio MCP / tests) every brand is read, exactly as before.
    allowed = get_allowed_brands()
    if brand:
        if allowed is not None and brand not in allowed:
            return []
        brands = [brand]
    else:
        brands = [
            p.name
            for p in sorted(root.iterdir())
            if p.is_dir() and not p.name.startswith("_")
        ]
        if allowed is not None:
            brands = [b for b in brands if b in allowed]

    records: list[AssetRecord] = []
    for slug in brands:
        for kind in _KINDS:
            # Confine the join: ``slug`` is already allow-list gated, but ``asset``
            # (the asset-type segment) is caller-supplied, so route the whole join
            # through the shared guard. A crafted segment that would escape the
            # workspace subtree yields None and is skipped, never scanned.
            parts = [slug, kind, asset] if asset else [slug, kind]
            scan_root = safe_subpath(root, *parts)
            if scan_root is None or not scan_root.exists():
                continue
            for sidecar, image_path in _iter_sidecars(scan_root):
                try:
                    data = json.loads(sidecar.read_text())
                except (OSError, ValueError):
                    continue
                # ``slug`` is the directory the file lives under, which is the
                # owning brand the URL must resolve against -- authoritative over a
                # sidecar ``brand`` field that may record a different source brand.
                record = _coerce_record(data, sidecar, image_path, kind=kind, brand=slug)
                if record is None:
                    continue
                if status is not None and record.lifecycle_status != status:
                    continue
                records.append(record)
    # Newest first when a timestamp is present; records without one sort last.
    records.sort(key=lambda r: r.created_at or "", reverse=True)
    return records
