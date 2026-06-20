"""Append-only, tamper-evident audit log for privileged admin actions.

The per-tenant activity feed (``brand_ops.activity``) records what happened
inside a workspace, and it lives under that workspace's own subtree, so it is
erased when the tenant is offboarded. The admin surface needs the opposite
property: a record of every privileged, cross-tenant act (standing a workspace
up, granting or revoking membership, exporting a tenant's data, and above all
DELETING a tenant) that OUTLIVES the tenant it touched and that a later operator
can prove was not edited after the fact.

This module is that record. It is a single append-only JSONL file stored at the
ROOT of the kits tree (``brand-kits/.admin-audit.jsonl``), deliberately OUTSIDE
any ``brand-kits/<workspace_id>/`` subtree, so an offboard that purges a tenant
subtree never removes the audit trail of that offboard. The leading dot keeps it
out of the brand listing the same way the activity feed's name does.

What it provides:

* ``append_admin_event`` writes one privileged action as a single chained JSON
  line: who acted (the admin's uc_uid), the action, the workspace it targeted, a
  short non-sensitive summary of the parameters, and the result. The DENY path is
  recorded too (a refused admin attempt is itself security-relevant), so the log
  answers "who tried what, and was it allowed".
* ``write_tombstone`` records an erasure tombstone for a workspace at the moment
  it is offboarded: the workspace id, slug, the acting admin, and the counts of
  what was removed. The tombstone is written into THIS root-level log, so the
  proof that a tenant existed and was deleted survives the deletion of all of
  that tenant's own data.
* ``verify_admin_chain`` walks the log and confirms the tamper-evident hash chain
  is intact, reporting the first break (an edited, deleted, or reordered line).

Tamper-evidence: every line carries a ``prev_hash`` (the prior line's
``entry_hash``) and an ``entry_hash`` (``sha256`` over the canonical entry body
plus that ``prev_hash``). Editing, deleting, or reordering any past line breaks
the chain at that point, which ``verify_admin_chain`` detects and locates. The
genesis sentinel, the canonical-JSON digest, and the in-process-lock plus
``fcntl.flock`` plus ``fsync`` critical section are the SAME design the activity
feed uses. ``brand_ops.activity`` is out of scope for this change, so rather than
import its internals the minimal chaining logic is duplicated here with this
note; the two logs stay independent files with independent chains.

Like the activity feed, this module calls no provider SDK and reads no secret,
and an append is best-effort: a write failure is logged, never raised into the
admin action it records, because a missing audit line must not abort an
offboard. Callers keep the parameter summary free of secrets (the audit captures
WHAT was done, not the bytes of an export or a token).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

try:  # POSIX advisory file lock, matching the activity feed's cross-process guard
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX; the in-process lock still applies
    fcntl = None

from .brand_loader import DEFAULT_KITS_DIR

# Serialize the read-prev-then-append critical section WITHIN this process, the
# same reason activity.append_event does: two concurrent admin appends (the sync
# routes run on a threadpool) must not both read the same last entry_hash and
# fork the chain, which would make verify_admin_chain report a permanent
# false-positive tamper break. The fcntl.flock below extends the guard across
# processes / replicas. This is a SEPARATE lock from the activity feed's because
# it guards a separate file with its own chain.
_APPEND_LOCK = threading.Lock()

# A structured logger so an audit write failure is a visible warning, not a
# silent swallow. Shares the "brand_ops" logger family the rest of the app wires.
_log = logging.getLogger("brand_ops.admin.audit")

# The admin-audit log lives at the ROOT of the kits tree, beside the per-workspace
# subtrees, NOT inside any one of them, so a workspace offboard (which purges
# brand-kits/<workspace_id>/) can never delete it. A leading dot keeps it out of
# the brand list and signals "infrastructure, not a brand", exactly like the
# activity feed's ".activity.jsonl".
_LOG_NAME = ".admin-audit.jsonl"

# The kinds of entry this log records, so a reader can filter by shape. Not a
# hard gate (the log must never reject a write): "action" is an ordinary
# privileged action, "tombstone" is the erasure record for an offboarded tenant.
ENTRY_KINDS = ("action", "tombstone")

# The genesis link: the prev_hash of the very first chained entry. A fixed
# sentinel (not an empty string) so a first entry is distinguishable from a line
# whose prev_hash was lost, and so the chain has a well-defined root. Identical
# in spirit to activity.GENESIS_PREV_HASH; duplicated here to keep this log's
# chain self-contained.
GENESIS_PREV_HASH = "0" * 64


def _log_path(kits_dir: Path | None = None) -> Path:
    """The on-disk path of the admin-audit log at the kits tree root.

    Resolved from ``brand_loader.DEFAULT_KITS_DIR`` (the GLOBAL root) at call
    time, never from the request-scoped per-workspace root, so the audit trail is
    always written to the one shared location regardless of which workspace (if
    any) the request happens to be scoped to. ``kits_dir`` overrides the root for
    tests, mirroring the activity feed's signature.
    """
    root = kits_dir or DEFAULT_KITS_DIR
    return root / _LOG_NAME


# ---------------------------------------------------------------------------
# Hash chain (duplicated from the activity feed's design; see module docstring)
# ---------------------------------------------------------------------------
#
# Each entry is ``{... , prev_hash, entry_hash}``. ``entry_hash`` is the sha256
# of the canonical JSON of the entry WITHOUT ``entry_hash`` (so it covers every
# field, including prev_hash). Because prev_hash is the prior entry's
# entry_hash, any edit to a past line changes that line's entry_hash, which no
# longer matches the prev_hash the next line recorded: the break
# verify_admin_chain reports. Deleting or reordering a line breaks it the same way.


def _entry_hash(entry: dict[str, Any]) -> str:
    """The sha256 hex digest over the canonical entry body (excluding entry_hash).

    ``sort_keys=True`` plus a compact separator gives one canonical byte string
    for a given entry, so the digest is stable regardless of dict insertion order
    and independent of how the JSON is written to disk.
    """
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_entry_hash(path: Path) -> str:
    """The entry_hash of the last chained line, or the genesis sentinel.

    Reads the existing log and returns the entry_hash of the most recent line
    that carries one, so the next append links to it. A missing or unreadable
    log, or one whose tail lines lack a chain, falls back to the genesis
    sentinel: the new entry simply starts (or restarts) the chain rather than
    failing the write. The whole helper is wrapped so chaining never blocks an
    append.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return GENESIS_PREV_HASH
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("entry_hash"), str) and obj["entry_hash"]:
            return obj["entry_hash"]
    return GENESIS_PREV_HASH


def _append(entry: dict[str, Any], kits_dir: Path | None) -> dict[str, Any]:
    """Stamp an id, link into the chain, and write one entry as a JSON line.

    The read-prev plus link plus append must be ONE atomic critical section, or
    two concurrent appends fork the chain. The in-process lock serializes threads
    (the sync-route threadpool); the exclusive fcntl.flock serializes processes /
    replicas; reading the last hash happens INSIDE both locks so prev_hash
    reflects any concurrent prior append; fsync makes the line durable. This is
    the activity feed's append discipline, duplicated here for this log's own
    file and chain (see the module docstring). Best-effort and total: any failure
    to build or write the entry is caught and logged, never raised, so an
    unwritable audit log can never abort the privileged action it records.
    """
    entry.setdefault("id", uuid.uuid4().hex)
    try:
        path = _log_path(kits_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _APPEND_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    entry["prev_hash"] = _last_entry_hash(path)
                    entry["entry_hash"] = _entry_hash(entry)
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    if fcntl is not None:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001 - audit write is best-effort; never abort the action
        # Never propagate, but never swallow silently either: a lost audit line is
        # an operational signal worth a warning. Exclude the params summary from
        # the message (it may name a tenant); the action and workspace are enough.
        _log.warning(
            "admin audit append failed; entry dropped: action=%s workspace=%s",
            entry.get("action"),
            entry.get("workspace_id"),
        )
    return entry


def append_admin_event(
    actor_uc_uid: str | None,
    action: str,
    workspace_id: str | None,
    params_summary: dict[str, Any] | None = None,
    result: str = "ok",
    *,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one privileged admin action to the tamper-evident audit log.

    Records a chained line
    ``{id, kind='action', actor_uc_uid, action, workspace_id, params_summary,
    result, prev_hash, entry_hash}`` and returns it. ``actor_uc_uid`` is the
    authenticated admin who acted (the route passes ``admin.uc_uid``).
    ``workspace_id`` is the tenant the action targeted (None for an action that
    is not workspace-specific). ``params_summary`` is a SMALL, secret-free dict
    describing the request (a slug, a member uc_uid, a role, a count), never the
    bytes of an export or any credential. ``result`` is a short outcome token:
    ``"ok"`` for a completed action, ``"denied"`` for a refused attempt (the deny
    path is recorded because a rejected privileged request is itself an audit
    event), or any other short status the caller chooses.

    Best-effort: a write failure is logged and the entry is still returned, so an
    unwritable audit log never aborts the action it records.
    """
    entry = {
        "kind": "action",
        "actor_uc_uid": actor_uc_uid,
        "action": action,
        "workspace_id": workspace_id,
        "params_summary": params_summary or {},
        "result": result,
    }
    return _append(entry, kits_dir)


def write_tombstone(
    workspace_id: str,
    slug: str | None,
    admin: str | None,
    counts: dict[str, Any] | None = None,
    *,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Record an erasure tombstone for an offboarded workspace.

    Written at the moment a tenant is deleted, INTO the root-level admin-audit log
    (not the tenant's own feed, which is about to be purged), so the durable proof
    that this workspace existed and was erased survives the erasure. Records a
    chained line ``{id, kind='tombstone', workspace_id, slug, actor_uc_uid,
    counts, prev_hash, entry_hash}`` and returns it. ``counts`` is the tally of
    what the offboard removed (for example db rows, on-disk files, brands), a
    small secret-free summary. The offboarding route writes the tombstone BEFORE
    (or as part of) the destructive steps, so a record exists even if a later step
    fails partway. Best-effort, like every append here.
    """
    entry = {
        "kind": "tombstone",
        "workspace_id": workspace_id,
        "slug": slug,
        # Named actor_uc_uid for symmetry with append_admin_event, so a reader can
        # filter "who acted" across both action and tombstone lines uniformly.
        "actor_uc_uid": admin,
        "counts": counts or {},
    }
    return _append(entry, kits_dir)


def verify_admin_chain(kits_dir: Path | None = None) -> dict[str, Any]:
    """Verify the tamper-evident hash chain over the admin-audit log.

    Walks the log in append order (oldest first) and checks, for every CHAINED
    line, that its ``entry_hash`` recomputes from its body and that its
    ``prev_hash`` equals the previous chained line's ``entry_hash``. The result is
    a report::

        {
          "ok": bool,               # the whole chain is intact
          "chained": int,           # lines that carry chain fields and were checked
          "legacy": int,            # un-chained lines tolerated (counted, not checked)
          "total": int,             # every JSON-object line seen
          "chain_start": int|None,  # index of the first chained line, or None
          "break_at": int|None,     # index of the first broken line, or None when ok
          "reason": str|None,       # why it broke ("entry_hash", "prev_hash"), or None
        }

    The walk tolerates un-chained ("legacy") lines (counted and skipped, not a
    break) so the verifier cannot be tripped by a hand-appended or pre-feature
    line. A break is an entry_hash that does not recompute (the line's body was
    edited) or a prev_hash that does not match the prior chained line (a line was
    deleted, inserted, or reordered). Same verification shape as
    ``activity.verify_chain``, over this log's own file (see the module docstring).
    """
    path = _log_path(kits_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        # No log is a vacuously intact chain.
        return {
            "ok": True,
            "chained": 0,
            "legacy": 0,
            "total": 0,
            "chain_start": None,
            "break_at": None,
            "reason": None,
        }

    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        rows.append(obj)

    chained = 0
    legacy = 0
    chain_start: int | None = None
    prev: str | None = None  # the entry_hash of the last verified chained line

    for index, obj in enumerate(rows):
        entry = obj.get("entry_hash")
        has_chain = isinstance(entry, str) and bool(entry) and "prev_hash" in obj
        if not has_chain:
            # A legacy / un-chained line: tolerated. It does not advance the chain
            # link, so a later chained line still verifies against the prior one.
            legacy += 1
            continue

        if chain_start is None:
            chain_start = index

        # 1) The line's own body must hash to its recorded entry_hash.
        if _entry_hash(obj) != entry:
            return {
                "ok": False,
                "chained": chained,
                "legacy": legacy,
                "total": len(rows),
                "chain_start": chain_start,
                "break_at": index,
                "reason": "entry_hash",
            }

        # 2) Its prev_hash must point at the previous chained line's entry_hash.
        #    The first chained line must point at the genesis sentinel.
        expected_prev = prev if prev is not None else GENESIS_PREV_HASH
        if obj.get("prev_hash") != expected_prev:
            return {
                "ok": False,
                "chained": chained,
                "legacy": legacy,
                "total": len(rows),
                "chain_start": chain_start,
                "break_at": index,
                "reason": "prev_hash",
            }

        prev = entry
        chained += 1

    return {
        "ok": True,
        "chained": chained,
        "legacy": legacy,
        "total": len(rows),
        "chain_start": chain_start,
        "break_at": None,
        "reason": None,
    }


def read_admin_events(
    limit: int = 100, kits_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Read admin-audit entries newest-first, capped at ``limit``.

    Reads the whole log, drops any line that is not a JSON object (a corrupt or
    truncated write), reverses to newest-first, and caps at ``limit``. A missing
    log is an empty list; a non-positive ``limit`` returns an empty list. Used by
    the tests (and any future admin read surface) to confirm a tombstone or
    action landed without reaching past the JSONL boundary.
    """
    if limit <= 0:
        return []
    path = _log_path(kits_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    entries: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    entries.reverse()
    return entries[:limit]


__all__ = (
    "append_admin_event",
    "write_tombstone",
    "verify_admin_chain",
    "read_admin_events",
    "GENESIS_PREV_HASH",
    "ENTRY_KINDS",
)
