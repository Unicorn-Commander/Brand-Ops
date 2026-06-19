"""Append-only activity / provenance feed for Brand-Ops.

Every meaningful act in the Studio -- a human approving an asset, the compliance
gate flagging one off-brand, an agent running a pack, the director routing an
intent -- lands here as one immutable line. The feed is the orientation surface:
it answers "what happened, who did it, when" without a database. It survives in
the image-built tree because it is just a JSONL file under the kits root
(``brand-kits/.activity.jsonl``).

Three things the feed is responsible for:

* ``append_event`` writes one event as a single JSON line. It is pure of any
  wall clock: the caller supplies the timestamp (the route layer stamps
  ``datetime.now(timezone.utc).isoformat()`` at the API boundary), so the helper
  itself is reproducible. It stamps a UUID id and never raises into its caller --
  a feed that cannot be written must never break the operation it records.
* ``read_events`` reads the log newest-first, optionally filtered by brand, with
  a limit. A missing file is an empty feed; a corrupt or truncated line is
  skipped rather than fatal, so one bad write can never poison a read.
* ``verify_chain`` walks the log and confirms the tamper-evident hash chain is
  intact, reporting the first break (an edited, deleted, or reordered line).

Tamper-evidence (T2.5): every event written by ``append_event`` carries a
``prev_hash`` (the ``entry_hash`` of the line before it) and an ``entry_hash``
(``sha256`` over the canonical event body plus that ``prev_hash``). Editing any
field of any past line, deleting a line, or reordering lines breaks the chain at
that point, which ``verify_chain`` detects and locates. The chain is append-only
and the helper stays best-effort: linking reads the prior line, so a write
failure still never propagates into the action being recorded.

Actor attribution (T2.5): the existing ``actor`` / ``actor_kind`` are the
PERSONA the feed shows ("you", "iris", "compliance"). The real authenticated
caller is stamped separately as ``uc_uid``, read from a request-scoped
contextvar that ``require_principal`` (REST) and the MCP request scope set from
the validated principal. An unscoped caller (the CLI, the stdio MCP, a test, or
AUTH off) leaves it null, which reads as "system".

This module calls no provider SDK and reads no secret. It is the only thing that
touches the log file, so the on-disk shape stays in one place.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .brand_loader import current_kits_dir

# A structured logger so a feed write failure is a visible warning, not a silent
# swallow. A sibling stream wires structlog; plain stdlib logging under the same
# "brand_ops" name interoperates with whatever handler is configured.
_log = logging.getLogger("brand_ops")

# The feed lives at the root of the kits tree, beside the per-brand kit
# directories, so it ships with the built image and is found without config. A
# leading dot keeps it out of the brand list (``list_brands`` skips ``_``- and
# dot-prefixed entries) and signals "infrastructure, not a brand".
_LOG_NAME = ".activity.jsonl"

# The actor kinds the feed recognizes. Not enforced as a hard gate (the feed
# must never reject a write), but the API surface validates against this set so
# the frontend can rely on it.
ACTOR_KINDS = ("human", "agent", "system")

# The genesis link: the ``prev_hash`` of the very first chained entry. A fixed
# sentinel (not an empty string) so a first entry is distinguishable from a line
# whose ``prev_hash`` was lost, and so the chain has a well-defined root.
GENESIS_PREV_HASH = "0" * 64

# ---------------------------------------------------------------------------
# Request-scoped real actor (the authenticated caller's uc_uid)
# ---------------------------------------------------------------------------
#
# ``actor`` / ``actor_kind`` are the persona the feed renders. The real caller
# is separate: ``require_principal`` (REST) and the MCP request scope publish the
# validated ``principal.uc_uid`` here, and ``append_event`` stamps it onto every
# event as ``uc_uid`` so each line records WHO acted, not just which persona the
# action was attributed to. A contextvar (not a module global) keeps it
# per-request and prevents bleed between concurrent async requests. Unset (the
# CLI, the stdio MCP, a test, AUTH off) is None, which reads as "system".
_actor: ContextVar[str | None] = ContextVar("brand_ops_actor_uid", default=None)


def set_current_actor(uc_uid: str | None):
    """Publish the authenticated caller's uc_uid for the life of the request.

    Called once at the auth boundary (``require_principal`` for REST, the MCP
    request scope for the HTTP tool surface) with the validated principal's
    ``uc_uid``. ``append_event`` reads it. Returns the contextvar Token so a
    caller may reset it; the request lifecycle normally lets it fall out of scope.
    Passing the anonymous caller (or None) records an unattributed "system" act.
    """
    return _actor.set(uc_uid)


def get_current_actor() -> str | None:
    """The authenticated caller's uc_uid for this request, or None when unscoped."""
    return _actor.get()


def reset_current_actor(token=None) -> None:
    """Clear the request-scoped actor.

    With a Token (from :func:`set_current_actor`) it restores the prior value;
    without one it hard-resets to None. Tests use the no-arg form so the actor
    never leaks between tests.
    """
    if token is not None:
        _actor.reset(token)
    else:
        _actor.set(None)


def _log_path(kits_dir: Path | None = None) -> Path:
    """The on-disk path of the activity log under the kits tree root."""
    root = kits_dir or current_kits_dir()
    return root / _LOG_NAME


# ---------------------------------------------------------------------------
# Hash chain
# ---------------------------------------------------------------------------
#
# Each entry is ``{... , prev_hash, entry_hash}``. ``entry_hash`` is the sha256
# of the canonical JSON of the entry WITHOUT ``entry_hash`` (so it covers every
# field, including ``prev_hash``). Because ``prev_hash`` is the prior entry's
# ``entry_hash``, any edit to a past line changes that line's ``entry_hash``,
# which no longer matches the ``prev_hash`` the next line recorded -- the break
# ``verify_chain`` reports. Deleting or reordering a line breaks it the same way.


def _entry_hash(event: dict[str, Any]) -> str:
    """The sha256 hex digest over the canonical event body (excluding entry_hash).

    ``sort_keys=True`` + a compact separator gives one canonical byte string for
    a given event, so the digest is stable regardless of dict insertion order and
    independent of how the JSON happens to be written to disk.
    """
    body = {k: v for k, v in event.items() if k != "entry_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_entry_hash(path: Path) -> str:
    """The ``entry_hash`` of the last chained line, or the genesis sentinel.

    Reads the existing log and returns the ``entry_hash`` of the most recent line
    that carries one, so the next append links to it. A missing or unreadable log,
    or one whose tail lines are legacy/un-chained, falls back to the genesis
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


def append_event(
    actor: str,
    actor_kind: str,
    action: str,
    *,
    brand: str | None = None,
    target: str | None = None,
    note: str | None = None,
    ts: str | None = None,
    uc_uid: str | None = None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one event to the activity log and return the event dict.

    Builds an event
    ``{id, ts, actor, actor_kind, uc_uid, action, brand, target, note,
    prev_hash, entry_hash}``, stamps a fresh UUID ``id``, links it into the
    tamper-evident hash chain (``prev_hash`` = the prior line's ``entry_hash``,
    ``entry_hash`` = the sha256 over this entry's canonical body), and writes it
    as one JSON line. ``ts`` is the caller's: this helper never reads a clock, so
    it stays reproducible and the wall-clock stamp lives at the API boundary. When
    ``ts`` is omitted the field is recorded as ``None`` rather than invented here.

    ``actor`` / ``actor_kind`` are the PERSONA the feed renders. ``uc_uid`` is the
    real authenticated caller: when not passed it defaults to the request-scoped
    actor (:func:`set_current_actor`, published at the auth boundary), so every
    event records who acted even though each call site keeps naming its persona.
    An unscoped caller (CLI / stdio MCP / test / AUTH off) records ``uc_uid`` as
    null, which reads as "system".

    Best-effort and total: any failure to build the event or write the line is
    caught and logged as a structured warning under the ``brand_ops`` logger (the
    event dict is still returned), because an unwritable feed must never propagate
    into the generation, upload, or transition it is recording.
    """
    event = {
        "id": uuid.uuid4().hex,
        "ts": ts,
        "actor": actor,
        "actor_kind": actor_kind,
        # The real authenticated caller, distinct from the rendered persona. A
        # caller may pass it explicitly; otherwise it is the request-scoped actor.
        "uc_uid": uc_uid if uc_uid is not None else get_current_actor(),
        "action": action,
        "brand": brand,
        "target": target,
        "note": note,
    }
    try:
        path = _log_path(kits_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Link into the chain just before writing, so prev_hash reflects whatever
        # is actually the last line on disk (including a concurrent prior append).
        event["prev_hash"] = _last_entry_hash(path)
        event["entry_hash"] = _entry_hash(event)
        # One event per line. ensure_ascii=False keeps brand names and notes
        # human-readable on disk; the line is newline-terminated so the next
        # append is a clean record even after a partial prior write.
        line = json.dumps(event, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - the feed is advisory; a write failure is never fatal
        # Never propagate, but never swallow silently either: a lost audit line is
        # an operational signal worth a warning. Exclude the body from the message
        # (a note could carry sensitive text); the exception type is enough.
        _log.warning(
            "activity append failed (advisory feed); event dropped: action=%s brand=%s",
            action,
            brand,
        )
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

    Tolerant by design: one bad line never breaks the read, and a legacy line
    that predates the hash chain (no ``prev_hash`` / ``entry_hash``) reads back
    exactly as before -- the chain fields are additive, never required to read.
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


def verify_chain(
    brand: str | None = None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Verify the tamper-evident hash chain over the activity log.

    Walks the log in append order (oldest first) and checks, for every CHAINED
    line, that its ``entry_hash`` recomputes from its body and that its
    ``prev_hash`` equals the previous chained line's ``entry_hash``. The result
    is a report::

        {
          "ok": bool,            # the whole chain is intact
          "chained": int,        # lines that carry chain fields and were checked
          "legacy": int,         # un-chained lines tolerated (counted, not checked)
          "total": int,          # every JSON-object line seen
          "chain_start": int|None,  # index of the first chained line, or None
          "break_at": int|None,  # index of the first broken line, or None when ok
          "reason": str|None,    # why it broke ("entry_hash", "prev_hash"), or None
        }

    The walk is backward-tolerant. Legacy lines that predate the chain (no
    ``entry_hash``) are counted and skipped, NOT treated as a break, and
    ``chain_start`` reports the index where the chain begins, so a log that mixes
    pre-feature and post-feature lines verifies cleanly from the point chaining
    began. A break is an ``entry_hash`` that does not recompute (the line's own
    body was edited) or a ``prev_hash`` that does not match the prior chained
    line (a line was deleted, inserted, or reordered). The optional ``brand``
    filters to one brand's lines first, then verifies the chain over just those.

    The ``index`` reported is the position among the JSON-object lines considered
    (after any brand filter), oldest-first and zero-based.
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
        if brand is not None and obj.get("brand") != brand:
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
