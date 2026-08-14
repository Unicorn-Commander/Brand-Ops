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

Both halves are async (they open asyncpg sessions), and every caller is sync, so
they are reached through the ``_sync`` bridges below. Those bridges are the
delicate part of this module: read their section note before changing them. A
bridge that fails to reach its coroutine cannot fail loudly -- metering is
best-effort by contract -- so it fails as an unenforced cap and a missing ledger
row, which looks exactly like a workspace that simply had nothing to bill.

Attribution. An image generation is the work of the brand director persona Iris,
which is also what the activity feed records for a generation and the governance
row the Crew page synthesizes. So image spend attributes to
``agent_kind='director'`` / ``agent_ref='iris'``, and a weekly budget set on
Iris in the Crew page governs image generation for the workspace.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta

from . import governance
from .backends.base import QuotaExceeded

__all__ = [
    "QuotaExceeded",
    "enforce_image_quota",
    "enforce_image_quota_sync",
    "image_cap_cents",
    "image_governance",
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
        # NullPool bridge sessionmaker: the gate runs on a throwaway asyncio.run /
        # governor loop in a threadpool worker, so it must not reuse the pooled
        # engine's loop-bound connections (see db.base.get_bridge_sessionmaker).
        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.repository import (
            agent_spend_total_since,
            workspace_image_spend_since,
        )

        sessionmaker = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
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
        # WARNING, not debug: the soft path is indistinguishable from "under the
        # cap" at every other level, so debug meant an unenforced cap left no
        # trace anyone would find.
        _log.warning(
            "image quota gate skipped for workspace %s (metering store unavailable); "
            "generation proceeds unchecked",
            workspace_id,
            exc_info=True,
        )


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
        # NullPool bridge sessionmaker (see db.base.get_bridge_sessionmaker): this
        # best-effort meter runs on a throwaway asyncio.run / governor loop in a
        # threadpool worker, so it must not reuse a pooled, loop-bound connection.
        from .db.base import get_bridge_sessionmaker, workspace_session
        from .db.repository import record_spend_event

        sessionmaker = get_bridge_sessionmaker()  # raises if DATABASE_URL is unset
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
        # WARNING, not debug: a dropped spend row is money the provider charged
        # and the ledger will never show. Best-effort means "do not raise", not
        # "do not tell anyone".
        _log.warning(
            "image spend NOT RECORDED for workspace %s (%s/%s, %s cents x%s)",
            workspace_id,
            backend,
            model,
            cost_cents,
            count,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Sync bridges for the synchronous generation chokepoint
# ---------------------------------------------------------------------------
#
# ``engine.generate_asset`` is synchronous, and so is every caller that carries a
# workspace -- but they do not all run on the same kind of thread:
#
#   * NO running loop: the CLI, the stdio MCP server, and the sync ``def`` REST
#     routes (``/generate``, the avatar routes), which FastAPI offloads to a
#     threadpool worker. ``asyncio.run`` is the right bridge here.
#   * A LIVE running loop: the HTTP MCP transport. ``api/mcp_http.py`` mounts the
#     same FastMCP instance over Streamable HTTP and FastMCP invokes the tools on
#     the ASGI event loop, so a tool body runs *inside* a loop.
#
# This section used to assume the first shape only and call ``asyncio.run``
# unconditionally. On the second shape -- which is what production serves,
# ``MCP_HTTP_ENABLED=true`` -- ``asyncio.run`` raises "cannot be called from a
# running event loop" the moment it is entered, i.e. AFTER the coroutine argument
# has been constructed but before anything awaits it. The coroutine was then
# dropped unawaited ("RuntimeWarning: coroutine 'meter_image_spend' was never
# awaited") and the broad ``except Exception`` swallowed the RuntimeError at
# DEBUG. Nothing raised, nothing was logged above debug, and every generation on
# that transport ran with the cap unenforced and the spend unrecorded.
#
# So the bridge PROBES for a running loop instead of assuming, exactly like the
# catalog dual-write in ``catalog_index.py`` already does after the same failure
# mode hid a broken write for three weeks. Two properties keep it honest:
#
#   * callers pass a zero-argument FACTORY, never a coroutine. The awaitable is
#     created on the thread that will drive it, so there is no window in which a
#     live coroutine exists without something already committed to awaiting it.
#     The bug above is not reachable through this helper.
#   * the failure paths log at WARNING, not debug. Metering is best-effort by
#     contract and must never raise, which leaves the log as the only channel a
#     silent money hole can announce itself on; debug is not a channel.
#
# An unscoped run (CLI / stdio MCP / tests / AUTH off) has no workspace, so both
# bridges still return before touching a loop or the database at all.

# How long a bridged DB touch may block the synchronous generation path. The gate
# runs immediately before the paid backend call and the meter immediately after
# it, so a wedged metering store must not pin the request forever: it degrades to
# "no cap" / "unrecorded, logged loudly". Matches the catalog dual-write budget.
_BRIDGE_TIMEOUT_S = 15.0


def _run_blocking[T](factory: Callable[[], Awaitable[T]], *, name: str) -> T:
    """Drive ``factory()`` to completion from sync code, whatever thread we are on.

    ``factory`` is invoked on the thread that owns the loop which will await it,
    so a coroutine is never built on one thread and abandoned on another.

    With no running loop, ``asyncio.run`` on this thread. With one (the HTTP MCP
    tools on the ASGI loop), a private loop on a worker thread, because
    ``asyncio.run`` cannot nest. Re-raises whatever the awaitable raised on the
    calling thread, or ``TimeoutError`` past ``_BRIDGE_TIMEOUT_S``.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[T] = []
    failure: list[BaseException] = []

    def _worker() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            failure.append(exc)

    thread = threading.Thread(target=_worker, name=name, daemon=True)
    thread.start()
    thread.join(timeout=_BRIDGE_TIMEOUT_S)
    if thread.is_alive():
        raise TimeoutError(f"{name} timed out after {_BRIDGE_TIMEOUT_S}s")
    if failure:
        raise failure[0]
    return result[0]


def enforce_image_quota_sync(workspace_id: str | None) -> None:
    """Synchronous gate the engine calls before generating. Raises QuotaExceeded.

    Bridges :func:`enforce_image_quota` for the sync engine, on whichever thread
    shape the caller happens to be (see the section note). Returns immediately
    when there is no active workspace, so the unscoped CLI / stdio MCP / test
    path never opens an event loop or a database connection. Only ``QuotaExceeded``
    (the intentional denial) propagates; a cap is a budget guard, not a
    correctness invariant, so a bridging error degrades to "no cap" rather than
    blocking a generation a budget guard was never meant to stop. That degrade is
    logged at WARNING: a gate that has quietly stopped gating is indistinguishable
    from a gate with nothing to do.
    """
    if not workspace_id:
        return
    try:
        _run_blocking(lambda: enforce_image_quota(workspace_id), name="image-quota-gate")
    except QuotaExceeded:
        raise
    except Exception:  # noqa: BLE001 - the gate is soft; bridging errors never block
        _log.warning(
            "image quota gate DID NOT RUN for workspace %s; generation proceeds unchecked",
            workspace_id,
            exc_info=True,
        )


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

    Bridges :func:`meter_image_spend` for the sync engine, on whichever thread
    shape the caller happens to be (see the section note). Never raises: a
    metering failure must not undo a generation that already succeeded. No-op
    without an active workspace (no tenant to bill), so the unscoped path never
    opens a loop. A failure is logged at WARNING because the provider has already
    charged for these bytes and the ledger is about to disagree.
    """
    if not workspace_id:
        return
    try:
        _run_blocking(
            lambda: meter_image_spend(
                workspace_id,
                brand_slug=brand_slug,
                backend=backend,
                model=model,
                cost_cents=cost_cents,
                count=count,
            ),
            name="image-spend-meter",
        )
    except Exception:  # noqa: BLE001 - metering is best-effort; never break a run
        _log.warning(
            "image spend NOT RECORDED for workspace %s (%s/%s, %s cents x%s); "
            "the provider billed this generation and the ledger will not show it",
            workspace_id,
            backend,
            model,
            cost_cents,
            count,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Per-generation governance handle (cap read, then meter write)
# ---------------------------------------------------------------------------
#
# ``engine.generate_asset`` wraps its backend call in ``image_governance`` so it
# has ONE object for the pair: ``enforce`` before the spend, ``meter`` after it.
# Both halves go through the same loop-aware bridges as every other caller.
#
# This used to open a private event loop for the pair and drive both coroutines
# on it with ``run_until_complete``, to keep asyncpg's pool alive across the two
# DB touches. That bought nothing -- both halves use ``get_bridge_sessionmaker``,
# which is deliberately ``NullPool`` and retains no connection between checkouts,
# so there was never a pool to carry over -- and it broke outright on any thread
# that was already running a loop, since ``run_until_complete`` refuses to drive
# a second loop there. That is the engine's own path under the HTTP MCP
# transport, so the chokepoint every surface funnels through was the one most
# exposed to the silent hole described in the section above.


class _ImageGovernor:
    """The cap gate and the spend meter for one generation.

    ``enforce`` denies an over-budget run before the paid backend (only
    ``QuotaExceeded`` propagates); ``meter`` records the spend after it (never
    raises). Both delegate to the ``_sync`` bridges, so they inherit the
    loop-probing and the loud failure logging. Both are no-ops without a
    workspace, where there is no tenant to meter and no loop or database is
    touched.
    """

    def __init__(self, workspace_id: str | None):
        self._workspace_id = workspace_id

    def enforce(self) -> None:
        """Deny over-budget BEFORE the backend runs. Raises ``QuotaExceeded``."""
        enforce_image_quota_sync(self._workspace_id)

    def meter(
        self,
        *,
        brand_slug: str | None,
        backend: str | None,
        model: str | None,
        cost_cents: float,
        count: int = 1,
    ) -> None:
        """Record the spend AFTER a successful generation. Never raises."""
        meter_image_spend_sync(
            self._workspace_id,
            brand_slug=brand_slug,
            backend=backend,
            model=model,
            cost_cents=cost_cents,
            count=count,
        )


@contextlib.contextmanager
def image_governance(workspace_id: str | None) -> Iterator[_ImageGovernor]:
    """Hand the engine one governance handle for the span of a generation.

    Enter once around the generate region: call ``enforce`` before the paid
    backend call and ``meter`` after the bytes are saved. Ordering is the
    caller's, by construction; this only supplies the handle. A no-op (no loop,
    no database) without an active workspace, so the unscoped CLI / stdio MCP /
    test path is unchanged.
    """
    yield _ImageGovernor(workspace_id)
