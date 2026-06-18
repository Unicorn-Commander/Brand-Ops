"""One-time migration: flat ``brand-kits/<slug>/`` -> per-workspace ``brand-kits/<ws>/<slug>/``.

The per-workspace storage layout isolates each tenant's brand files on disk. This
moves an existing flat volume into it.

Non-destructive by default: the copy pass builds the per-workspace layout BESIDE
the flat one (copy-if-missing), so the running app keeps serving its flat data
until you verify the new layout and then run ``--cleanup`` to remove the flat
copies. Idempotent: re-running copies only what is missing, and an already-migrated
brand (under a uuid-named workspace dir) is skipped.

Placement: a brand goes to the workspace its ``brand.yaml`` records
(``brand.workspace_id``), else the deploy's bootstrap workspace
(``BRAND_OPS_BOOTSTRAP_WORKSPACE_ID``). On a sole-tenant volume every brand
belongs to that one workspace, so everything lands in its subtree.

Usage (inside the backend container, with the kits volume mounted and the
bootstrap workspace env set):

    python -m brand_ops.migrate_storage --dry-run   # report the plan, change nothing
    python -m brand_ops.migrate_storage             # copy (non-destructive)
    python -m brand_ops.migrate_storage --cleanup   # remove flat dirs that have a verified copy
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import uuid
from pathlib import Path

import yaml

from .brand_loader import DEFAULT_KITS_DIR

# The global activity feed moves into the bootstrap workspace's subtree (it is the
# sole tenant's history on a pre-migration volume).
_ACTIVITY = ".activity.jsonl"


def _coerce_uuid(value: object) -> str | None:
    """Canonical uuid string, or None. Inlined so this script needs no DB import."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _brand_workspace(brand_dir: Path, bootstrap_ws: str) -> str:
    """The workspace a flat brand belongs to: its own recorded id, else bootstrap."""
    try:
        data = yaml.safe_load((brand_dir / "brand.yaml").read_text()) or {}
        ws = _coerce_uuid((data.get("brand") or {}).get("workspace_id"))
    except (OSError, ValueError):
        ws = None
    return ws or bootstrap_ws


def _copy_missing(src: Path, dst: Path) -> int:
    """Recursively copy files from src into dst only where missing. Returns count."""
    copied = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        target = dst / item.name
        if item.is_dir():
            copied += _copy_missing(item, target)
        elif not target.exists():
            shutil.copy2(item, target)
            copied += 1
    return copied


def flat_brands(root: Path) -> list[Path]:
    """Top-level entries that are flat (un-migrated) brand dirs.

    A workspace subtree (a uuid-named dir) is skipped, as are infrastructure
    entries (``_``- or dot-prefixed) and anything without a ``brand.yaml``.
    """
    out: list[Path] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith(("_", ".")):
            continue
        if _coerce_uuid(p.name) is not None:
            continue  # already a workspace subtree
        if (p / "brand.yaml").exists():
            out.append(p)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report the plan, change nothing")
    ap.add_argument("--cleanup", action="store_true", help="remove flat dirs with a verified copy")
    ap.add_argument(
        "--root", default=str(DEFAULT_KITS_DIR), help="kits root (default: the live dir)"
    )
    args = ap.parse_args()

    root = Path(args.root)
    bootstrap_ws = _coerce_uuid(os.getenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "").strip() or None)
    if bootstrap_ws is None:
        print("ERROR: BRAND_OPS_BOOTSTRAP_WORKSPACE_ID must be set to a workspace uuid")
        sys.exit(2)
    if not root.is_dir():
        print(f"no kits root at {root}; nothing to do")
        return

    brands = flat_brands(root)
    act = root / _ACTIVITY
    act_dst = root / bootstrap_ws / _ACTIVITY

    if args.dry_run:
        if not brands and not act.exists():
            print("nothing to migrate: the volume is already per-workspace")
            return
        for src in brands:
            ws = _brand_workspace(src, bootstrap_ws)
            print(f"COPY  {src.name}  ->  {ws}/{src.name}")
        if act.exists():
            print(f"COPY  {_ACTIVITY}  ->  {bootstrap_ws}/{_ACTIVITY}")
        print(f"\n{len(brands)} brand(s) would move; nothing changed (dry run).")
        return

    if args.cleanup:
        removed = 0
        for src in brands:
            ws = _brand_workspace(src, bootstrap_ws)
            dst = root / ws / src.name
            # Top up the copy first so cleanup can never remove a flat dir whose
            # per-workspace copy is incomplete.
            _copy_missing(src, dst)
            if (dst / "brand.yaml").exists():
                shutil.rmtree(src)
                removed += 1
                print(f"removed flat {src.name} (verified copy at {ws}/{src.name})")
            else:
                print(f"SKIP {src.name}: no verified copy at {ws}/{src.name}")
        if act.exists() and act_dst.exists():
            act.unlink()
            print(f"removed flat {_ACTIVITY}")
        print(f"\ncleanup done: {removed} flat brand dir(s) removed.")
        return

    # Default: the non-destructive copy pass.
    if not brands and not act.exists():
        print("nothing to migrate: the volume is already per-workspace")
        return
    total_files = 0
    for src in brands:
        ws = _brand_workspace(src, bootstrap_ws)
        dst = root / ws / src.name
        n = _copy_missing(src, dst)
        total_files += n
        print(f"copied {src.name} -> {ws}/{src.name} ({n} new file(s))")
    if act.exists() and not act_dst.exists():
        act_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(act, act_dst)
        print(f"copied {_ACTIVITY} -> {bootstrap_ws}/{_ACTIVITY}")
    print(
        f"\nmigration copy done: {len(brands)} brand(s), {total_files} new file(s). "
        "Verify the app, then run --cleanup to remove the flat copies."
    )


if __name__ == "__main__":
    main()
