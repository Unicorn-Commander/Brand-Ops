"""Seed the live brand-kits directory from the baked seed, on container boot.

Persistence model: the image bakes the curated seed brands at /app/brand-kits-seed
(BRAND_KITS_SEED). The LIVE data lives at DEFAULT_KITS_DIR (/app/brand-kits),
which on a server is a persistent named volume. On boot we copy seed files into
the live dir COPY-IF-MISSING: a file that does not yet exist in the live dir is
copied in, but an existing file is NEVER overwritten and nothing is ever deleted.

That guarantees no data loss across redeploys: generated assets, agent
identities, and uploaded files all survive, and brand-new seed brands (or new
seed files) get added. The one trade-off is that an EDIT to an already-seeded
brand's seed file (e.g. a brand.yaml change) does not auto-propagate to a live
dir that already has it; push such an update by removing that file/brand from the
volume so the next boot re-seeds it. Durability of generated work is the priority.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .brand_loader import DEFAULT_KITS_DIR


def _copy_missing(src: Path, dst: Path) -> int:
    """Recursively copy files from src into dst only where they are missing."""
    copied = 0
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            copied += _copy_missing(item, target)
        elif not target.exists():
            shutil.copy2(item, target)
            copied += 1
    return copied


def seed(
    seed_dir: Path | None = None,
    live_dir: Path | None = None,
    workspace_subdir: str | None = None,
) -> int:
    """Seed live_dir from seed_dir, copy-if-missing. Returns files copied.

    Under the per-workspace layout the curated seed brands belong to the deploy's
    bootstrap workspace (Magic Unicorn), so when ``workspace_subdir`` is given
    (the bootstrap workspace id) they are copied into ``live_dir/<workspace_id>/``,
    not the flat root. A new tenant therefore starts with an empty brand library,
    not a copy of the suite's brands. With no ``workspace_subdir`` (local / CLI /
    a deploy with no tenancy configured) the original flat root is seeded.
    """
    seed_dir = seed_dir or Path(os.getenv("BRAND_KITS_SEED", "/app/brand-kits-seed"))
    live_dir = live_dir or DEFAULT_KITS_DIR
    if workspace_subdir:
        live_dir = live_dir / workspace_subdir
    if not seed_dir.is_dir():
        return 0
    live_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for brand in sorted(seed_dir.iterdir()):
        if brand.is_dir():
            total += _copy_missing(brand, live_dir / brand.name)
    return total


def main() -> None:
    seed_dir = Path(os.getenv("BRAND_KITS_SEED", "/app/brand-kits-seed"))
    ws = os.getenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "").strip() or None
    n = seed(workspace_subdir=ws)
    dest = DEFAULT_KITS_DIR / ws if ws else DEFAULT_KITS_DIR
    print(f"seed_kits: copied {n} missing file(s) from {seed_dir} -> {dest}")


if __name__ == "__main__":
    main()
