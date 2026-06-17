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
from pathlib import Path
from urllib.parse import urlencode

from ..brand_loader import (
    DEFAULT_KITS_DIR,
    get_allowed_brands,
    workspace_query_suffix,
)
from .schemas import AssetRecord


def _coerce_record(
    data: dict, sidecar: Path, image_path: Path, kind: str = "generated"
) -> AssetRecord | None:
    """Build an AssetRecord from a sidecar dict. None if it is not a sidecar."""
    if not isinstance(data, dict) or "asset_type" not in data:
        return None
    size = data.get("size")
    size_list = [int(x) for x in size] if isinstance(size, list) else []
    refs = data.get("reference_images")
    ref_list = [str(r) for r in refs] if isinstance(refs, list) else []
    brand = str(data.get("brand", ""))
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


def read_assets(
    brand: str | None = None,
    asset: str | None = None,
    kits_dir: Path | None = None,
    status: str | None = None,
) -> list[AssetRecord]:
    """Read catalog records, optionally filtered by brand, asset type, and status.

    Walks both the generated and imported trees under each brand kit. Reads only
    the directories implied by the filters, and skips unreadable or non-sidecar
    JSON files rather than raising, so one bad file cannot break a list.

    ``status`` filters by lifecycle status ("approved"/"needs_review"): a record
    whose ``lifecycle_status`` differs (including a never-stamped null) is
    dropped. Absent, every record is returned regardless of lifecycle status.
    """
    root = kits_dir or DEFAULT_KITS_DIR
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
            scan_root = root / slug / kind / asset if asset else root / slug / kind
            if not scan_root.exists():
                continue
            for sidecar, image_path in _iter_sidecars(scan_root):
                try:
                    data = json.loads(sidecar.read_text())
                except (OSError, ValueError):
                    continue
                record = _coerce_record(data, sidecar, image_path, kind=kind)
                if record is None:
                    continue
                if status is not None and record.lifecycle_status != status:
                    continue
                records.append(record)
    # Newest first when a timestamp is present; records without one sort last.
    records.sort(key=lambda r: r.created_at or "", reverse=True)
    return records
