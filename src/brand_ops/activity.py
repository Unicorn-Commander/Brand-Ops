"""Append-only activity / provenance feed for Brand-Ops.

Every meaningful act in the Studio — a human approving an asset, the compliance
gate flagging one off-brand, an agent running a pack, the director routing an
intent — lands here as one immutable line. The feed is the orientation surface:
it answers "what happened, who did it, when" without a database. It survives in
the image-built tree because it is just a JSONL file under the kits root
(``brand-kits/.activity.jsonl``).

Two operations, both deliberately small and forgiving:

* ``append_event`` writes one event as a single JSON line. It is pure of any
  wall clock: the caller supplies the timestamp (the route layer stamps
  ``datetime.now(timezone.utc).isoformat()`` at the API boundary), so the helper
  itself is reproducible. It stamps a UUID id and never raises into its caller —
  a feed that cannot be written must never break the operation it records.
* ``read_events`` reads the log newest-first, optionally filtered by brand, with
  a limit. A missing file is an empty feed; a corrupt or truncated line is
  skipped rather than fatal, so one bad write can never poison a read.

This module calls no provider SDK and reads no secret. It is the only thing that
touches the log file, so the on-disk shape stays in one place.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .brand_loader import DEFAULT_KITS_DIR

# The feed lives at the root of the kits tree, beside the per-brand kit
# directories, so it ships with the built image and is found without config. A
# leading dot keeps it out of the brand list (``list_brands`` skips ``_``- and
# dot-prefixed entries) and signals "infrastructure, not a brand".
_LOG_NAME = ".activity.jsonl"

# The actor kinds the feed recognizes. Not enforced as a hard gate (the feed
# must never reject a write), but the API surface validates against this set so
# the frontend can rely on it.
ACTOR_KINDS = ("human", "agent", "system")


def _log_path(kits_dir: Path | None = None) -> Path:
    """The on-disk path of the activity log under the kits tree root."""
    root = kits_dir or DEFAULT_KITS_DIR
    return root / _LOG_NAME


def append_event(
    actor: str,
    actor_kind: str,
    action: str,
    *,
    brand: str | None = None,
    target: str | None = None,
    note: str | None = None,
    ts: str | None = None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one event to the activity log and return the event dict.

    Builds an event ``{id, ts, actor, actor_kind, action, brand, target, note}``,
    stamps a fresh UUID ``id``, and writes it as one JSON line. ``ts`` is the
    caller's: this helper never reads a clock, so it stays reproducible and the
    wall-clock stamp lives at the API boundary. When ``ts`` is omitted the field
    is recorded as ``None`` rather than invented here.

    Best-effort and total: any failure to build the event or write the line is
    swallowed (the event dict is still returned), because an unwritable feed must
    never propagate into the generation, upload, or transition it is recording.
    """
    event = {
        "id": uuid.uuid4().hex,
        "ts": ts,
        "actor": actor,
        "actor_kind": actor_kind,
        "action": action,
        "brand": brand,
        "target": target,
        "note": note,
    }
    try:
        path = _log_path(kits_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # One event per line. ensure_ascii=False keeps brand names and notes
        # human-readable on disk; the line is newline-terminated so the next
        # append is a clean record even after a partial prior write.
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - the feed is advisory; a write failure is never fatal
        pass
    return event


def read_events(
    brand: str | None = None,
    limit: int = 50,
    kits_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Read activity events newest-first, optionally filtered by brand.

    Reads the whole log, parses each line, drops any line that is not a JSON
    object (a corrupt or truncated write), optionally keeps only events for the
    given ``brand``, reverses to newest-first, and caps at ``limit``. A missing
    log is an empty feed. A non-positive ``limit`` returns an empty list; there
    is no unbounded read.

    Tolerant by design: one bad line never breaks the read, mirroring how the
    catalog skips an unreadable sidecar rather than failing a list.
    """
    if limit <= 0:
        return []
    path = _log_path(kits_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []

    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            # A truncated or corrupt line; skip it rather than fail the feed.
            continue
        if not isinstance(obj, dict):
            continue
        if brand is not None and obj.get("brand") != brand:
            continue
        events.append(obj)

    # Newest first: the log is append-order (oldest first), so reverse. The cap
    # is applied after the reverse so a limit returns the most recent N.
    events.reverse()
    return events[:limit]
