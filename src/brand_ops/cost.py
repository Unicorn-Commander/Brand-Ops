"""Per-tenant image cost governance: meter every image, enforce a cap first.

Image generation runs on a shared gateway/provider key. Without per-tenant
accounting one workspace can drain the budget for all of them, and an agent's
``weekly_budget_cents`` was stored but never enforced. This module closes both
gaps at the one generation chokepoint (``engine.generate_asset``), so the REST
API, the MCP server, the CLI, and the autonomy agents are all covered without
editing any of their files.

Two halves, both deliberately defensive:

  * :func:`enforce_image_quota` runs BEFORE the backend call. It sums this
    workspace's image spend over a rolling window and denies (raises
    ``QuotaExceeded``) when a per-workspace cap or the attributed agent's weekly
    budget is already met. It is the gate.

  * :func:`meter_image_spend` runs AFTER a successful generation. It appends one
    ``agent_spend_event`` (``kind='image'``) so the next request's gate, the
    Crew page rollup, and Lago reconciliation all see the charge. It mirrors the
    text path's ``_meter_chat`` and is best-effort: a metering failure never
    undoes a generation that already succeeded.

DEFAULT DISABLED. The cap env defaults to 0 (no cap) and agent budgets default
to null (no cap), so a deploy that sets neither behaves exactly as before: the
gate is a no-op and metering only adds rows. No new migration: both halves reuse
``agent_spend_event`` and ``agent_governance``.

Attribution. An image generation is the work of the brand director persona Iris,
which is also what the activity feed records for a generation and the governance
row the Crew page synthesizes. So image spend attributes to
``agent_kind='director'`` / ``agent_ref='iris'``, and a weekly budget set on
Iris in the Crew page governs image generation for the workspace.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from . import governance
from .backends.base import QuotaExceeded

__all__ = [
    "QuotaExceeded",
    "enforce_image_quota",
    "enforce_image_quota_sync",
    "image_cap_cents",
    "meter_image_spend",
    "meter_image_spend_sync",
]

_log = logging.getLogger("brand_ops.cost")

# The agent an image generation is attributed to (see the module docstring): the
# brand director persona, matching the activity feed and the synthesized Crew
# governance row, so a weekly budget set on Iris governs image spend.
IMAGE_AGENT_KIND = "director"
IMAGE_AGENT_REF = "iris"

# The rolling window the per-workspace image cap accumulates over. A calendar
# month of spend is the natural budgeting period for a shared image key; it is
# generous enough that the cap, when set, is a real ceiling rather than a
# per-request throttle. The agent weekly-budget gate uses a 7-day window to match
# the Crew page's "7-day spend" rollup, so the number the operator sees is the
# number the gate enforces.
_IMAGE_WINDOW_DAYS = 30
_BUDGET_WINDOW_DAYS = 7


def image_cap_cents() -> int:
    """The per-workspace image spend cap in US cents; 0 (the default) = no cap.

    Read directly from ``BRAND_OPS_IMAGE_CAP_CENTS`` at call time (not cached, so
    a deploy can change it without a restart and a test can monkeypatch the env).
    A missing, empty, non-integer, or non-positive value reads as 0 = disabled,
    so a misconfigured cap can never accidentally block every generation; it just
    leaves the gate off, exactly the pre-feature behavior.
    """
    raw = os.getenv("BRAND_OPS_IMAGE_CAP_CENTS", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        _log.warning(
            "BRAND_OPS_IMAGE_CAP_CENTS=%r is not an integer; treating cap as disabled",
            raw,
        )
        return 0
    return value if value > 0 else 0


async def _agent_weekly_budget_cents(scoped, workspace_id: str) -> int | None:
    """The attributed agent's ``weekly_budget_cents`` for this workspace, or None.

    None when the agent has no governance row or an unset budget = no cap (the
    default, unchanged behavior). Read inside the caller's already-scoped
    transaction so it sees only this workspace's row under RLS.
    """
    from .db.repository import get_agent_governance

    row = await get_agent_governance(
        scoped,
        workspace_id,
        agent_kind=IMAGE_AGENT_KIND,
        agent_ref=IMAGE_AGENT_REF,
    )
    return row.weekly_budget_cents if row is not None else None


async def enforce_image_quota(workspace_id: str | None) -> None:
    """Deny an image generation that would breach a per-workspace cap or budget.

    Called by the engine BEFORE the backend runs. Two independent gates, each
    off by default so nothing breaks until an operator opts in:

      1. Per-workspace image cap: when ``BRAND_OPS_IMAGE_CAP_CENTS`` is set and
         this workspace's image spend in the rolling window already meets it.
      2. Agent weekly budget: when Iris's ``weekly_budget_cents`` is set and the
         agent's spend in the 7-day window already meets it.

    Raises ``QuotaExceeded`` (a ``BackendError`` subclass, so the existing
    handlers map it to a clean 4xx) with a message naming which limit was hit and
    by how much. No-op and returns when:

      * there is no active workspace (the CLI, the stdio MCP, tests, AUTH off):
        an unscoped run has no tenant to meter, so it keeps the original global
        behavior;
      * ``DATABASE_URL`` is unset (``get_sessionmaker`` raises) or the DB is
        unreachable: this is a SOFT gate, deliberately. A cap is a budget guard,
        not a correctness invariant, so a metering-store outage must not take
        generation down with it. The failure is logged, not raised, and only the
        ``QuotaExceeded`` we intentionally raise propagates.
    """
    if not workspace_id:
        return

    cap = image_cap_cents()

    try:
        from .db.base import get_sessionmaker, workspace_session
        from .db.repository import (
            agent_spend_total_since,
            workspace_image_spend_since,
        )

        sessionmaker = get_sessionmaker()  # raises if DATABASE_URL is unset
        now = datetime.now(UTC)
        async with sessionmaker() as session:
            async with workspace_session(session, workspace_id) as scoped:
                if cap > 0:
                    since = now - timedelta(days=_IMAGE_WINDOW_DAYS)
                    spent = await workspace_image_spend_since(
                        scoped, workspace_id, since
                    )
                    if governance.over_cap(spent, cap):
                        raise QuotaExceeded(
                            f"workspace image budget reached: "
                            f"{spent:.2f} of {cap} cents used in the last "
                            f"{_IMAGE_WINDOW_DAYS} days. Raise "
                            f"BRAND_OPS_IMAGE_CAP_CENTS or wait for the window "
                            f"to roll over."
                        )

                budget = await _agent_weekly_budget_cents(scoped, workspace_id)
                if budget:
                    since = now - timedelta(days=_BUDGET_WINDOW_DAYS)
                    agent_spent = await agent_spend_total_since(
                        scoped,
                        workspace_id,
                        since,
                        agent_kind=IMAGE_AGENT_KIND,
                        agent_ref=IMAGE_AGENT_REF,
                    )
                    if governance.over_cap(agent_spent, budget):
                        raise QuotaExceeded(
                            f"agent weekly budget reached for "
                            f"{IMAGE_AGENT_REF}: {agent_spent:.2f} of {budget} "
                            f"cents used in the last {_BUDGET_WINDOW_DAYS} days. "
                            f"Raise the agent's weekly budget on the Crew page."
                        )
    except QuotaExceeded:
        # The intentional denial: re-raise so the engine surfaces it. Everything
        # below catches only the soft failures (no DB, transport, RLS), never
        # this.
        raise
    except Exception:  # noqa: BLE001 - the gate is soft; a store outage never blocks
        _log.debug("image quota gate skipped (metering store unavailable)", exc_info=True)


async def meter_image_spend(
    workspace_id: str | None,
    *,
    brand_slug: str | None,
    backend: str | None,
    model: str | None,
    cost_cents: float,
    count: int = 1,
) -> None:
    """Record one best-effort image spend event for a successful generation.

    Mirrors the text path's ``_meter_chat``: appends an ``agent_spend_event``
    (``kind='image'``) attributed to the Iris director persona, carrying the
    estimated ``cost_cents`` and the model/backend, scoped to ``workspace_id``.
    Everything is lazy-imported and wrapped so a metering failure (no workspace,
    no ``DATABASE_URL``, an RLS or transport error) is silently dropped: a broken
    image path is unacceptable, missing one spend row is not. No-op without an
    active workspace (the CLI / stdio MCP / tests), where there is no tenant to
    bill.
    """
    if not workspace_id:
        return
    try:
        from .db.base import get_sessionmaker, workspace_session
        from .db.repository import record_spend_event

        sessionmaker = get_sessionmaker()  # raises if DATABASE_URL is unset
        async with sessionmaker() as session:
            async with workspace_session(session, workspace_id) as scoped:
                await record_spend_event(
                    scoped,
                    workspace_id,
                    agent_kind=IMAGE_AGENT_KIND,
                    agent_ref=IMAGE_AGENT_REF,
                    kind="image",
                    brand_slug=brand_slug,
                    model=model,
                    cost_cents=governance.estimate_image_cost_cents(cost_cents, count),
                )
    except Exception:  # noqa: BLE001 - metering is best-effort; never break a run
        _log.debug("image spend metering skipped", exc_info=True)


# ---------------------------------------------------------------------------
# Sync bridges for the synchronous generation chokepoint
# ---------------------------------------------------------------------------
#
# ``engine.generate_asset`` is synchronous, and so are both of its callers that
# carry a workspace: the REST ``/generate`` route (a sync ``def``, which FastAPI
# runs in a threadpool worker with no event loop in that thread) and the MCP
# ``generate_asset`` tool (also sync, no live loop). So ``asyncio.run`` is the
# correct bridge in every workspace-scoped context here -- the same pattern the
# MCP server already uses for its DB writes. An unscoped run (CLI / stdio MCP /
# tests / AUTH off) has no workspace, so both bridges return before touching a
# loop at all.


def enforce_image_quota_sync(workspace_id: str | None) -> None:
    """Synchronous gate the engine calls before generating. Raises QuotaExceeded.

    Bridges :func:`enforce_image_quota` for the sync engine. Returns immediately
    when there is no active workspace, so the unscoped CLI / stdio MCP / test
    path never opens an event loop or a database connection. Only ``QuotaExceeded``
    (the intentional denial) propagates; the async gate already swallows soft
    failures (no DB, transport) and the bridge itself swallows nothing it should
    not, so an unexpected bridging error is logged and treated as "no cap" rather
    than blocking a generation a budget guard was never meant to stop.
    """
    if not workspace_id:
        return
    try:
        asyncio.run(enforce_image_quota(workspace_id))
    except QuotaExceeded:
        raise
    except Exception:  # noqa: BLE001 - the gate is soft; bridging errors never block
        _log.debug("image quota gate skipped (bridge error)", exc_info=True)


def meter_image_spend_sync(
    workspace_id: str | None,
    *,
    brand_slug: str | None,
    backend: str | None,
    model: str | None,
    cost_cents: float,
    count: int = 1,
) -> None:
    """Synchronous best-effort metering the engine calls after a generation.

    Bridges :func:`meter_image_spend` for the sync engine. Never raises: a
    metering failure must not undo a generation that already succeeded. No-op
    without an active workspace (no tenant to bill), so the unscoped path never
    opens a loop.
    """
    if not workspace_id:
        return
    try:
        asyncio.run(
            meter_image_spend(
                workspace_id,
                brand_slug=brand_slug,
                backend=backend,
                model=model,
                cost_cents=cost_cents,
                count=count,
            )
        )
    except Exception:  # noqa: BLE001 - metering is best-effort; never break a run
        _log.debug("image spend metering skipped (bridge error)", exc_info=True)
