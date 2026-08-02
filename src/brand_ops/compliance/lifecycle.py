"""Asset lifecycle gate built on the deterministic compliance loop.

Every catalog asset carries a lifecycle status derived from its compliance
verdict: an asset that FAILS the deterministic checker needs a human review;
anything else (pass / warn / skipped) is approved. ``stamp_compliance`` runs the
checker once and writes that verdict back into the asset's sidecar JSON, so the
gate is recorded at creation time (generation, upload) and re-derivable in a
backfill. The override path (an operator approving a flagged asset) is the
``/api/assets/approve`` route, not here.

Like the rest of the compliance package this is pure-ish and LLM-free: it loads
a brand, builds its rules, scores one asset, and touches that one asset's
sidecar (read-modify-write, preserving every other key). A missing brand,
sidecar, or image degrades gracefully to ``{"lifecycle_status": None}`` rather
than raising, so a stamping failure can never break a generation or an upload.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..activity import append_event
from ..atomic_write import atomic_write_text
from ..brand_loader import (
    BrandNotFound,
    current_kits_dir,
    get_current_workspace_id,
    load_brand,
)
from ..catalog_index import index_sidecar_sync
from .evaluator import _resolve_image_path, check_asset
from .rules import build_rules

# The lifecycle states an asset can carry. The compliance gate writes
# needs_review / approved at creation time (fail -> needs_review, otherwise
# approved); the rest are reached only by an operator or agent transition. The
# API validates a requested ``to`` against this set before touching disk.
#   concept           -> made and explored, NOT approved for production (a draft)
#   needs_review      -> the compliance gate flagged it off-brand
#   approved          -> cleared for production use
#   changes_requested -> sent back for edits
#   archived          -> retired from use
LIFECYCLE_STATES = (
    "concept",
    "needs_review",
    "approved",
    "changes_requested",
    "archived",
)

# The states whose assets are NOT live production identity. The GUI groups these
# into a secondary "concepts & drafts" shelf so the production assets read clean.
NON_PRODUCTION_STATES = ("concept", "changes_requested", "archived")


def _lifecycle_for(status: str) -> str:
    """Map a compliance status to a lifecycle status.

    Only an outright ``fail`` needs review; pass, warn, and skipped are approved.
    """
    return "needs_review" if status == "fail" else "approved"


def _now_iso() -> str:
    """A UTC ISO-8601 timestamp for an activity event stamped from this layer.

    ``append_event`` itself never reads a clock, so the time is stamped here when
    the lifecycle layer (not an HTTP route) is the event's origin — the creation
    stamp from ``stamp_compliance``. The route layer stamps its own ``ts`` and
    passes it through instead.
    """
    return datetime.now(UTC).isoformat()


def stamp_compliance(
    brand_slug: str,
    asset_type: str,
    file: str,
    kind: str,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Score one asset and write its lifecycle verdict into its sidecar.

    Loads the brand, builds its rules, runs the deterministic ``check_asset``,
    derives ``lifecycle_status`` ("needs_review" when the compliance status is
    "fail", else "approved"), and merges ``lifecycle_status`` plus a small
    ``compliance`` summary ``{status, score, violations}`` into the asset's
    sidecar JSON. Read-modify-write: every other key in the sidecar is preserved.

    Returns the compliance summary that was written:
    ``{"lifecycle_status", "status", "score", "violations"}``. Degrades
    gracefully — a missing brand, sidecar, or unreadable sidecar returns
    ``{"lifecycle_status": None}`` and writes nothing, so a stamping failure
    never propagates into the caller (generation/upload stay best-effort).

    Emits an activity event ONLY when the verdict flags the asset off-brand
    (needs_review). A clear (approved) writes nothing to the feed: it is the
    common case and a no-op for the operator, so logging it would flood the
    feed — especially during a bulk backfill of mostly-on-brand assets.
    """
    image_path = _resolve_image_path(brand_slug, file, asset_type, kind, kits_dir)
    if image_path is None:  # unsafe/escaping path: stamp nothing
        return {"lifecycle_status": None}
    sidecar = image_path.with_suffix(image_path.suffix + ".json")
    if not sidecar.is_file():
        return {"lifecycle_status": None}

    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return {"lifecycle_status": None}
    if not isinstance(data, dict):
        return {"lifecycle_status": None}

    try:
        brand = load_brand(brand_slug, kits_dir)
    except (BrandNotFound, ValueError):
        return {"lifecycle_status": None}
    rules = build_rules(brand)

    result = check_asset(brand_slug, file, asset_type, kind, rules, kits_dir)
    compliance = {
        "status": result["status"],
        "score": int(result["score"]),
        "violations": list(result["violations"]),
    }
    lifecycle_status = _lifecycle_for(result["status"])

    data["lifecycle_status"] = lifecycle_status
    data["compliance"] = compliance
    try:
        atomic_write_text(sidecar, json.dumps(data, indent=2))
    except OSError:  # pragma: no cover - defensive; sidecar was just read
        return {"lifecycle_status": None}

    # Record the gate's verdict on the provenance feed ONLY when it flags an
    # asset off-brand (needs_review). A clear (approved) is the common case and a
    # no-op for the operator, so emitting it would flood the feed — most of all
    # during a bulk backfill of mostly-on-brand assets. The feed is a signal of
    # things that need attention, not a log of every check. Best-effort and after
    # the write, so a feed failure never undoes the stamp and a stamp failure
    # never emits a misleading flag.
    if lifecycle_status == "needs_review":
        try:
            append_event(
                "compliance",
                "system",
                "flagged off-brand",
                brand=brand_slug,
                target=f"{asset_type}/{file}",
                note=f"{result['status']} ({int(result['score'])})",
                ts=_now_iso(),
                kits_dir=kits_dir,
            )
        except Exception:  # noqa: BLE001 - the feed is advisory; never fail the stamp
            pass

    return {"lifecycle_status": lifecycle_status, **compliance}


def transition(
    brand_slug: str,
    asset_type: str,
    file: str,
    kind: str,
    to: str,
    *,
    note: str | None = None,
    actor: str = "you",
    ts: str | None = None,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Move one asset to a new lifecycle state and record it on the feed.

    Validates ``to`` against ``LIFECYCLE_STATES`` (a bad state raises
    ``ValueError`` so the route can map it to a 422), resolves the asset's
    sidecar, and read-modify-writes ``lifecycle_status=to`` into it, preserving
    every other key. This is the operator/agent path: ``approved`` (accepting a
    flagged asset), ``changes_requested`` (sending it back), ``archived``
    (retiring it), or ``needs_review`` (re-opening).

    On success it appends one activity event describing the move and returns the
    refreshed sidecar dict so the caller can build a catalog record. ``ts`` is
    the event's timestamp: the route layer passes the instant it stamped at the
    HTTP boundary; when omitted (a direct domain call) a UTC stamp is taken here.
    A missing sidecar raises ``FileNotFoundError`` (the route maps it to a 404);
    an unreadable or non-record sidecar raises ``ValueError`` (a 422). The
    activity append is best-effort and never undoes a written transition.
    """
    if to not in LIFECYCLE_STATES:
        allowed = ", ".join(LIFECYCLE_STATES)
        raise ValueError(f"unknown lifecycle state {to!r}. Allowed: {allowed}")

    image_path = _resolve_image_path(brand_slug, file, asset_type, kind, kits_dir)
    # Confine the resolved sidecar to the kits root, matching the asset API's
    # defense in depth: a crafted ``file``/``asset_type`` cannot reach outside the
    # tree (``_resolve_image_path`` returns None on an escaping segment).
    if image_path is None:
        raise FileNotFoundError("asset sidecar not found")
    root = (kits_dir or current_kits_dir()).resolve()
    sidecar = image_path.with_suffix(image_path.suffix + ".json")
    if not sidecar.is_relative_to(root) or not sidecar.is_file():
        raise FileNotFoundError("asset sidecar not found")

    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"sidecar is unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("sidecar is not an asset record")

    data["lifecycle_status"] = to
    atomic_write_text(sidecar, json.dumps(data, indent=2))

    # Dual-write the new status into Postgres, the same SoR transition the
    # generation and upload chokepoints do. Under BRAND_OPS_CATALOG_SOT=db the
    # Library reads the catalog ROW, not the sidecar, so a transition that only
    # touched disk was invisible: approving a flagged asset returned
    # lifecycle_status="approved" while the product kept showing it as
    # needs_review forever. Best-effort and loud-on-failure, exactly like the
    # other two call sites -- a broken index must not undo a transition whose
    # sidecar already landed.
    index_sidecar_sync(
        get_current_workspace_id(),
        sidecar_path=sidecar,
        workspace_root=root,
        kind=kind,
    )

    # One feed event per transition. The action reads like a verb in the feed:
    # "approved", "requested changes", "archived", "reopened".
    try:
        append_event(
            actor,
            _actor_kind_for(actor),
            _ACTION_FOR_STATE.get(to, to),
            brand=brand_slug,
            target=f"{asset_type}/{file}",
            note=note,
            ts=ts or _now_iso(),
            kits_dir=kits_dir,
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the transition
        pass

    return data


# A transition's target state mapped to the human-readable action verb the feed
# shows. Kept beside ``transition`` so the feed's vocabulary lives in one place.
_ACTION_FOR_STATE = {
    "approved": "approved",
    "changes_requested": "requested changes",
    "archived": "archived",
    "needs_review": "reopened",
}


def _actor_kind_for(actor: str) -> str:
    """Classify an actor name as human, agent, or system for the feed.

    A best-effort heuristic so a transition driven by a named agent (e.g. the
    director "iris" or "asset_pack") is tagged ``agent`` while an operator
    ("you") is ``human``. The route can pass an explicit actor; callers that
    care about the kind can set the actor accordingly.
    """
    known_agents = {"iris", "director", "asset_pack", "brand_critic", "brand_research"}
    if actor == "compliance":
        return "system"
    if actor in known_agents:
        return "agent"
    return "human"


def backfill_compliance(kits_dir: Path | None = None) -> dict[str, int]:
    """Stamp every catalog asset with its lifecycle verdict.

    Iterates the whole catalog via ``read_assets`` and calls
    ``stamp_compliance`` on each asset, so a kit imported or generated before the
    lifecycle gate existed gets its ``lifecycle_status`` populated in place.
    Returns counts: ``{"stamped", "needs_review", "approved"}`` (``stamped`` is
    the number of assets whose sidecar was actually written; an asset that
    degraded to a null status is not counted).
    """
    # Imported here (not at module top) to keep the compliance package free of an
    # import cycle: catalog imports schemas, not the other way round, but the API
    # layer is a heavier dependency than this pure helper should pull in eagerly.
    from ..api.catalog import read_assets

    stamped = 0
    needs_review = 0
    approved = 0
    for record in read_assets(kits_dir=kits_dir):
        summary = stamp_compliance(
            record.brand, record.asset_type, record.file, record.kind, kits_dir
        )
        status = summary.get("lifecycle_status")
        if status is None:
            continue
        stamped += 1
        if status == "needs_review":
            needs_review += 1
        else:
            approved += 1
    return {"stamped": stamped, "needs_review": needs_review, "approved": approved}


def _main() -> None:  # pragma: no cover - thin CLI shim, exercised via the Typer cmd
    """Run the backfill over the default kits dir and print the counts."""
    counts = backfill_compliance()
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":  # pragma: no cover
    _main()
