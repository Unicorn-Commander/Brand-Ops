"""Tests for the audit-chain verifier CLI (brand_ops.audit_verify).

Covers: a fresh node with no logs verifies clean; real appended chains verify
clean; a tampered activity OR admin line is detected and flips the exit code to
1; and non-uuid entries under the kits root are skipped.
"""
from __future__ import annotations

import json
from pathlib import Path

from brand_ops import admin_audit
from brand_ops.activity import append_event
from brand_ops.audit_verify import main, verify_all

_WS = "11111111-1111-1111-1111-111111111111"


def _activity_log(ws_dir: Path) -> Path:
    return ws_dir / ".activity.jsonl"


def _build_activity(ws_dir: Path, n: int = 3) -> None:
    ws_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        append_event("iris", "agent", f"act-{i}", brand="acme", kits_dir=ws_dir)


def _build_admin(root: Path, n: int = 3) -> None:
    for i in range(n):
        admin_audit.append_admin_event("admin1", f"action-{i}", _WS, kits_dir=root)


def _tamper(log_path: Path, which: int = 1) -> None:
    """Edit one chained line's body without updating its entry_hash -> break."""
    lines = log_path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[which])
    obj["action"] = str(obj.get("action", "")) + "-tampered"
    lines[which] = json.dumps(obj)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_clean_when_no_logs(tmp_path: Path) -> None:
    # A fresh node (no logs at all) is vacuously intact: every chain ok, exit 0.
    reports = verify_all(tmp_path)
    assert all(r["ok"] for r in reports)
    assert {r["chain"] for r in reports} == {"admin-audit", "activity:root"}
    assert main(["--kits-dir", str(tmp_path)]) == 0


def test_real_chains_verify_clean(tmp_path: Path) -> None:
    _build_admin(tmp_path)
    _build_activity(tmp_path / _WS)
    reports = verify_all(tmp_path)
    assert all(r["ok"] for r in reports)
    assert f"activity:{_WS}" in {r["chain"] for r in reports}
    assert main(["--kits-dir", str(tmp_path)]) == 0


def test_detects_activity_tamper(tmp_path: Path) -> None:
    ws_dir = tmp_path / _WS
    _build_activity(ws_dir, n=3)
    _tamper(_activity_log(ws_dir), which=1)

    reports = verify_all(tmp_path)
    broken = [r for r in reports if not r["ok"]]
    assert len(broken) == 1
    assert broken[0]["chain"] == f"activity:{_WS}"
    assert broken[0]["reason"] == "entry_hash"
    assert main(["--kits-dir", str(tmp_path)]) == 1


def test_detects_admin_tamper(tmp_path: Path) -> None:
    _build_admin(tmp_path, n=3)
    _tamper(tmp_path / ".admin-audit.jsonl", which=1)

    reports = verify_all(tmp_path)
    broken = [r for r in reports if not r["ok"]]
    assert len(broken) == 1
    assert broken[0]["chain"] == "admin-audit"
    assert main(["--kits-dir", str(tmp_path)]) == 1


def test_skips_non_uuid_dirs(tmp_path: Path) -> None:
    _build_activity(tmp_path / _WS)
    # A non-uuid sibling (e.g. the shared _template kit) must not become a chain.
    (tmp_path / "_template").mkdir()
    (tmp_path / "_template" / "brand.yaml").write_text("name: x\n")
    labels = {r["chain"] for r in verify_all(tmp_path)}
    assert f"activity:{_WS}" in labels
    assert "activity:_template" not in labels
