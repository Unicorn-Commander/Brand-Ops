"""Verify the tamper-evident audit hash chains; exit non-zero on any break.

The activity feed (``brand_ops.activity`` -- one ``.activity.jsonl`` per
workspace, plus a root log for unscoped events) and the admin-audit log
(``brand_ops.admin_audit`` -- one global ``.admin-audit.jsonl`` at the kits
root) are append-only sha256 hash chains: each chained line links to the
previous by hash, so an edit, delete, insert, or reorder breaks the chain and
``verify_chain`` / ``verify_admin_chain`` report exactly where.

Until now the only caller was the on-demand admin route
``GET /api/admin/workspaces/{id}/audit/verify`` -- nothing ran the check
proactively, so tampering would go unnoticed until someone happened to look.
This is the scheduled verifier: run it from cron on each node and alert on a
non-zero exit. It verifies the global admin-audit chain, the root activity
chain, and EACH workspace's activity chain (walking ``brand-kits/<workspace_id>/``
exactly as the retention sweep does), reading only the log files -- no database,
no workspace scope, safe to run anytime.

    docker exec brand-ops-backend python -m brand_ops.audit_verify
    docker exec brand-ops-backend python -m brand_ops.audit_verify --quiet   # only breaks

Exit codes: 0 = every chain intact, 1 = at least one chain broken (TAMPER),
2 = the verifier itself failed to run (treat as needs-attention, never "clean").
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from . import admin_audit
from .activity import verify_chain
from .db.tenancy import coerce_uuid

_log = logging.getLogger("brand_ops.audit_verify")


def verify_all(kits_dir: Path | None = None) -> list[dict[str, Any]]:
    """Verify every audit chain; return one labelled report per chain.

    Each report is the ``verify_chain`` / ``verify_admin_chain`` dict plus a
    ``"chain"`` label: ``"admin-audit"``, ``"activity:root"``, or
    ``"activity:<workspace_id>"``. A missing log is a vacuously intact chain
    (``ok=True``), so a fresh node reports clean rather than failing. Reads only
    files, so it is safe to call unscoped and needs no DB.
    """
    from .brand_loader import DEFAULT_KITS_DIR

    root = kits_dir or DEFAULT_KITS_DIR
    reports: list[dict[str, Any]] = []

    # 1) The global admin-audit chain (one log at the kits root).
    admin_report = dict(admin_audit.verify_admin_chain(root))
    admin_report["chain"] = "admin-audit"
    reports.append(admin_report)

    # 2) The root activity chain (unscoped / legacy events, if any).
    root_activity = dict(verify_chain(kits_dir=root))
    root_activity["chain"] = "activity:root"
    reports.append(root_activity)

    # 3) Each workspace's activity chain.
    if root.is_dir():
        for ws_dir in sorted(root.iterdir()):
            ws_id = coerce_uuid(ws_dir.name) if ws_dir.is_dir() else None
            if ws_id is None:
                # A non-uuid entry (a stray file or the shared _template kit) has no
                # per-workspace activity chain of its own; skip it.
                continue
            rep = dict(verify_chain(kits_dir=ws_dir))
            rep["chain"] = f"activity:{ws_id}"
            reports.append(rep)

    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the audit hash chains; exit 1 if any chain is broken."
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only broken chains, so a clean cron run is silent",
    )
    parser.add_argument(
        "--kits-dir",
        type=Path,
        default=None,
        help="override the brand-kits root (defaults to the configured volume)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    try:
        reports = verify_all(args.kits_dir)
    except Exception as exc:  # noqa: BLE001 - a verifier crash must never read as "clean"
        _log.error("audit verification failed to run: %s", exc.__class__.__name__)
        return 2

    breaks = [r for r in reports if not r["ok"]]
    for r in reports:
        if r["ok"]:
            if not args.quiet:
                print(f"  OK   {r['chain']}: {r['chained']} chained, {r['legacy']} legacy")
        else:
            print(
                f"  FAIL {r['chain']}: break at index {r['break_at']} "
                f"({r['reason']}); {r['chained']} chained, {r['total']} total"
            )

    if breaks:
        print(f"TAMPER: {len(breaks)} of {len(reports)} chain(s) BROKEN")
        return 1
    if not args.quiet:
        print(f"all {len(reports)} chain(s) intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
