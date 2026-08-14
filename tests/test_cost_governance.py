"""Tests for per-tenant image cost governance (T1.2).

Two layers, mirroring tests/test_governance.py:

  1. Pure unit tests (always run, no Postgres, no network): the cap arithmetic,
     the per-image cost estimate, the ``BRAND_OPS_IMAGE_CAP_CENTS`` reader, the
     ``QuotaExceeded`` -> ``BackendError`` relationship, and the default-disabled
     / unscoped / no-database SOFT behavior that guarantees nothing breaks until
     an operator sets a cap.

  2. Postgres-backed tests that drive the real spend ledger + RLS: a metered
     image generation writes an ``agent_spend_event(kind='image')``; the
     per-workspace cap denies over budget and passes under it; the agent weekly
     budget gate denies when set and is a no-op when null. SKIPPED unless
     ``BRAND_OPS_RUN_DB_TESTS=1`` and ``DATABASE_URL`` point at the
     ``brand_ops_app`` (NOBYPASSRLS) role on a migrated database, exactly like
     tests/test_db_tenancy.py. This file does not run that layer in CI; the
     parent runs the full suite.
"""
from __future__ import annotations

import gc
import logging
import os
import uuid
import warnings
from datetime import UTC, datetime, timedelta

import pytest

from brand_ops import cost, governance
from brand_ops.backends.base import (
    BackendError,
    GenRequest,
    GenResult,
    QuotaExceeded,
)

# ---------------------------------------------------------------------------
# over_cap: the pure cap-vs-spend arithmetic
# ---------------------------------------------------------------------------


def test_over_cap_disabled_when_cap_is_none_or_zero() -> None:
    # The default-disabled contract: a null or zero cap is "no cap", so even huge
    # spend is never over it. This is what keeps the feature off until an operator
    # sets BRAND_OPS_IMAGE_CAP_CENTS / a weekly budget.
    assert governance.over_cap(0.0, None) is False
    assert governance.over_cap(1_000_000.0, None) is False
    assert governance.over_cap(1_000_000.0, 0) is False
    assert governance.over_cap(1_000_000.0, -5) is False


def test_over_cap_is_an_inclusive_ceiling() -> None:
    # A positive cap is enforced inclusively: equal-to denies (the cap is a real
    # ceiling the window cannot pass), below passes, above denies.
    assert governance.over_cap(99.0, 100) is False
    assert governance.over_cap(99.9999, 100) is False
    assert governance.over_cap(100.0, 100) is True
    assert governance.over_cap(100.01, 100) is True
    assert governance.over_cap(250.0, 100) is True


# ---------------------------------------------------------------------------
# estimate_image_cost_cents: never None, prefers the backend's own number
# ---------------------------------------------------------------------------


def test_estimate_image_cost_prefers_backend_cost() -> None:
    # When the backend reports a cost (the litellm backend does, already times the
    # image count), record exactly that -- count is ignored, the backend already
    # multiplied.
    assert governance.estimate_image_cost_cents(4.0) == 4.0
    assert governance.estimate_image_cost_cents(17.0, count=5) == 17.0
    assert governance.estimate_image_cost_cents(0.0) == 0.0


def test_estimate_image_cost_falls_back_when_backend_reports_nothing() -> None:
    # A backend that reports no cost (openrouter returns None) must still record a
    # non-zero charge times the image count, never a fabricated zero that would
    # let a tenant generate under any cap for free.
    assert governance.estimate_image_cost_cents(None) == 4.0
    assert governance.estimate_image_cost_cents(None, count=3) == 12.0
    assert governance.estimate_image_cost_cents(None, count=0) == 4.0  # floored at 1
    # A negative backend number is treated as "no usable cost" -> fallback.
    assert governance.estimate_image_cost_cents(-1.0) == 4.0


def test_estimate_image_cost_is_never_none() -> None:
    # Unlike the per-token text estimator (which returns None with no usage), the
    # image estimate always returns a number: an image turn always records spend.
    assert governance.estimate_image_cost_cents(None) is not None
    assert governance.estimate_image_cost_cents(4.2) is not None


# ---------------------------------------------------------------------------
# image_cap_cents: the env reader, default-disabled and misconfig-safe
# ---------------------------------------------------------------------------


def test_image_cap_defaults_to_zero_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unset / empty -> 0 = disabled. This is the default that keeps the gate off.
    monkeypatch.delenv("BRAND_OPS_IMAGE_CAP_CENTS", raising=False)
    assert cost.image_cap_cents() == 0
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "")
    assert cost.image_cap_cents() == 0
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "   ")
    assert cost.image_cap_cents() == 0


def test_image_cap_reads_a_positive_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "500")
    assert cost.image_cap_cents() == 500
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", " 1200 ")
    assert cost.image_cap_cents() == 1200


def test_image_cap_misconfig_reads_as_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-integer or non-positive value must never accidentally block every
    # generation; it just leaves the gate off (the pre-feature behavior).
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "garbage")
    assert cost.image_cap_cents() == 0
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "12.5")
    assert cost.image_cap_cents() == 0
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "0")
    assert cost.image_cap_cents() == 0
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "-100")
    assert cost.image_cap_cents() == 0


# ---------------------------------------------------------------------------
# QuotaExceeded maps cleanly: it is a BackendError so existing handlers catch it
# ---------------------------------------------------------------------------


def test_quota_exceeded_is_a_backend_error() -> None:
    # The safety net: subclassing BackendError means the existing REST/MCP
    # handlers map a quota denial to a clean 4xx with no edit to those files. The
    # lead additionally maps it to 429 at integration.
    assert issubclass(QuotaExceeded, BackendError)
    err = QuotaExceeded("over budget")
    assert isinstance(err, BackendError)
    assert str(err) == "over budget"


# ---------------------------------------------------------------------------
# Default-disabled / unscoped / no-database SOFT behavior (no Postgres needed)
# ---------------------------------------------------------------------------


def test_enforce_is_noop_without_a_workspace() -> None:
    # Unscoped (CLI / stdio MCP / tests / AUTH off): no tenant to meter, so the
    # gate returns immediately without opening a loop or a DB connection, even
    # with a cap set in the env. The original global behavior is preserved.
    os.environ["BRAND_OPS_IMAGE_CAP_CENTS"] = "1"
    try:
        cost.enforce_image_quota_sync(None)  # must not raise
        cost.enforce_image_quota_sync("")  # empty string also unscoped
    finally:
        os.environ.pop("BRAND_OPS_IMAGE_CAP_CENTS", None)


def test_meter_is_noop_without_a_workspace() -> None:
    # No workspace -> nothing to bill -> a silent no-op, never an error.
    cost.meter_image_spend_sync(
        None, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
    )


def test_enforce_is_soft_when_database_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cap is a budget guard, not a correctness invariant. With a workspace set
    # and a cap configured but no DATABASE_URL (get_sessionmaker raises), the gate
    # must NOT take generation down: it logs and behaves as "no cap". This is the
    # guarantee that turning the feature on cannot brick a keyless/standalone run.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "1")
    # Reset the lazily-built engine/sessionmaker so get_settings re-reads the env.
    from brand_ops.db import base as dbbase

    monkeypatch.setattr(dbbase, "_engine", None, raising=False)
    monkeypatch.setattr(dbbase, "_sessionmaker", None, raising=False)
    cost.enforce_image_quota_sync(str(uuid.uuid4()))  # must not raise
    cost.meter_image_spend_sync(
        str(uuid.uuid4()),
        brand_slug="acme",
        backend="litellm",
        model="gpt-image-1",
        cost_cents=4.0,
    )


# ---------------------------------------------------------------------------
# The bridges ON A LIVE EVENT LOOP (no Postgres: the coroutines are spied).
#
# Every bridge test above this line calls the ``_sync`` helpers from a plain sync
# test function, i.e. from a thread with no running loop. Production does not
# always look like that: MCP_HTTP_ENABLED=true mounts the MCP tools on the ASGI
# event loop, so ``generate_asset`` and the avatar helpers run INSIDE a loop.
#
# In that shape the bridges used to call ``asyncio.run``, which raises before
# awaiting, leaving the already-constructed coroutine to be garbage-collected
# with "RuntimeWarning: coroutine 'meter_image_spend' was never awaited" -- and
# the broad best-effort ``except`` swallowed it. The cap never enforced and the
# spend was never recorded, and no test could see it, because no test ran the
# bridges from inside a loop. These do.
#
# They assert the two halves of the contract separately, because either one alone
# would still have passed while the money leaked: that the coroutine actually RAN
# (a bridge that silently no-ops is the bug), and that no coroutine was dropped
# unawaited (the fingerprint the bug left in the logs).
# ---------------------------------------------------------------------------


def _drive_and_collect(fn) -> list[str]:
    """Run ``fn`` and return the 'was never awaited' RuntimeWarnings it produced.

    The warning fires when the abandoned coroutine is COLLECTED, not when it is
    dropped, so the collection is forced inside the ``catch_warnings`` block --
    otherwise it can surface in an unrelated later test, or not at all.
    """
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fn()
        gc.collect()
    return [
        str(w.message)
        for w in caught
        if issubclass(w.category, RuntimeWarning) and "never awaited" in str(w.message)
    ]


@pytest.fixture
def spied_coroutines(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Replace both async halves with recorders that must actually be awaited."""
    calls: dict[str, list] = {"enforce": [], "meter": []}

    async def _enforce(workspace_id):
        calls["enforce"].append(workspace_id)

    async def _meter(workspace_id, **kw):
        calls["meter"].append((workspace_id, kw))

    monkeypatch.setattr(cost, "enforce_image_quota", _enforce)
    monkeypatch.setattr(cost, "meter_image_spend", _meter)
    return calls


@pytest.mark.asyncio
async def test_sync_bridges_reach_their_coroutine_from_inside_a_loop(
    spied_coroutines: dict[str, list],
) -> None:
    # The regression: on the HTTP MCP transport the bridges ran on the ASGI loop
    # and reached neither coroutine. Both must run, with the arguments intact.
    ws = str(uuid.uuid4())
    cost.enforce_image_quota_sync(ws)
    cost.meter_image_spend_sync(
        ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
    )

    assert spied_coroutines["enforce"] == [ws]
    assert len(spied_coroutines["meter"]) == 1
    metered_ws, kw = spied_coroutines["meter"][0]
    assert metered_ws == ws
    assert kw["cost_cents"] == 4.0
    assert kw["model"] == "gpt-image-1"


@pytest.mark.asyncio
async def test_image_governance_reaches_both_halves_from_inside_a_loop(
    spied_coroutines: dict[str, list],
) -> None:
    # The engine chokepoint's own path: generate_asset wraps the paid backend call
    # in image_governance, and under the HTTP MCP transport that ran on the ASGI
    # loop, where the governor's private-loop run_until_complete could not drive
    # anything at all.
    ws = str(uuid.uuid4())
    with cost.image_governance(ws) as governor:
        governor.enforce()
        governor.meter(
            brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
        )

    assert spied_coroutines["enforce"] == [ws]
    assert len(spied_coroutines["meter"]) == 1


@pytest.mark.asyncio
async def test_bridges_never_drop_a_coroutine_unawaited(
    spied_coroutines: dict[str, list],
) -> None:
    # The literal fingerprint of the defect, asserted directly: not one
    # "coroutine ... was never awaited" from any bridge while a loop is running.
    ws = str(uuid.uuid4())

    def _exercise_everything() -> None:
        cost.enforce_image_quota_sync(ws)
        cost.meter_image_spend_sync(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
        )
        with cost.image_governance(ws) as governor:
            governor.enforce()
            governor.meter(
                brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
            )

    assert _drive_and_collect(_exercise_everything) == []


@pytest.mark.asyncio
async def test_quota_denial_still_propagates_from_inside_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate has to keep DENYING across the bridge, not merely keep running:
    # QuotaExceeded is raised on the worker thread and must reach the caller.
    async def _deny(workspace_id):
        raise QuotaExceeded("workspace image budget reached")

    monkeypatch.setattr(cost, "enforce_image_quota", _deny)
    with pytest.raises(QuotaExceeded):
        cost.enforce_image_quota_sync(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_meter_failure_is_loud_but_never_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Metering stays best-effort (a dead ledger must not undo a generation that
    # already succeeded), but "best-effort" cost 54 days of unrecorded spend when
    # it meant debug-level silence. A dropped row must land in the log at WARNING.
    async def _explode(workspace_id, **kw):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(cost, "meter_image_spend", _explode)
    with caplog.at_level(logging.WARNING, logger="brand_ops.cost"):
        cost.meter_image_spend_sync(
            str(uuid.uuid4()),
            brand_slug="acme",
            backend="litellm",
            model="gpt-image-1",
            cost_cents=4.0,
        )
    assert any("NOT RECORDED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_unscoped_bridges_touch_nothing_even_inside_a_loop(
    spied_coroutines: dict[str, list],
) -> None:
    # The no-workspace contract is unchanged by the loop-probing: an unscoped run
    # has no tenant to meter, so neither coroutine is even constructed.
    cost.enforce_image_quota_sync(None)
    cost.meter_image_spend_sync(
        None, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
    )
    with cost.image_governance(None) as governor:
        governor.enforce()
        governor.meter(
            brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
        )

    assert spied_coroutines == {"enforce": [], "meter": []}


# ---------------------------------------------------------------------------
# Engine chokepoint wiring (no Postgres: the cost calls are spied/stubbed).
# Proves generate_asset enforces BEFORE the backend and meters AFTER success,
# and that a QuotaExceeded denial stops the generation before the backend runs.
# ---------------------------------------------------------------------------


class _FakeBackend:
    """A healthy backend that records one image. Mirrors test_backend_litellm."""

    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self._healthy = healthy
        self.generated = False
        self.last_user: object = None

    def health(self) -> bool:
        return self._healthy

    def generate(self, req: GenRequest) -> GenResult:
        self.generated = True
        self.last_user = req.extra.get("user")
        return GenResult(
            images=[b"PNG"], mime="image/png", backend=self.name, model="m", cost_cents=4.0
        )


class _FakeGovernor:
    """Stand-in for cost._ImageGovernor that records enforce/meter on one object.

    The engine now wraps the cap read and the meter write in the
    ``cost.image_governance`` context manager (one event loop for the pair); these
    tests spy on it instead of the per-half ``_sync`` bridges. ``enforce`` and
    ``meter`` delegate to caller-supplied callbacks so each test asserts ordering,
    the forwarded workspace id, and the metered kwargs exactly as before.
    """

    def __init__(self, workspace_id, on_enforce, on_meter):
        self._ws = workspace_id
        self._on_enforce = on_enforce
        self._on_meter = on_meter

    def enforce(self) -> None:
        self._on_enforce(self._ws)

    def meter(self, **kw) -> None:
        self._on_meter(self._ws, **kw)


def _patch_governor(monkeypatch, on_enforce, on_meter) -> None:
    """Point engine.cost.image_governance at a _FakeGovernor for one test."""
    import contextlib

    @contextlib.contextmanager
    def _fake(workspace_id):
        yield _FakeGovernor(workspace_id, on_enforce, on_meter)

    from brand_ops import engine

    monkeypatch.setattr(engine.cost, "image_governance", _fake)


def _engine_settings():
    from brand_ops.config import Settings

    return Settings(
        litellm_base_url="http://gw:4000",
        litellm_api_key="sk-virtual",
        litellm_image_model="gpt-image-1",
    )


def _real_kits_dir():
    """The real flat brand-kits root.

    Setting a workspace scope points current_kits_dir() at the per-tenant subtree
    brand-kits/<ws>/, where the seed brands do not live. These engine tests want
    a real workspace id (for the gate/meter) but the flat seed brands, so they
    pass this root explicitly; load_brand uses ``kits_dir or current_kits_dir()``.
    """
    from brand_ops.brand_loader import DEFAULT_KITS_DIR

    return DEFAULT_KITS_DIR


def test_engine_enforces_before_and_meters_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chokepoint calls enforce (with the workspace) then meters the result.

    Stubs save_result and the backend so no files or network are touched, spies
    on the cost gate + meter, and scopes the request to a workspace. The gate
    must be called before generation and the meter after, both carrying the
    active workspace id; and the litellm-style backend must receive that id as
    the OpenAI ``user`` field for central attribution.
    """
    from brand_ops import brand_loader, engine

    calls: list[str] = []
    seen: dict[str, object] = {}

    fake = _FakeBackend("litellm", healthy=True)
    monkeypatch.setattr(engine, "load_backend", lambda name, settings: fake)
    monkeypatch.setattr(engine, "save_result", lambda *a, **k: [])

    def _enforce(ws: str | None) -> None:
        calls.append("enforce")
        seen["enforce_ws"] = ws
        assert not fake.generated, "gate must run before the backend"

    def _meter(ws: str | None, **kw: object) -> None:
        calls.append("meter")
        seen["meter_ws"] = ws
        seen["meter_kw"] = kw

    _patch_governor(monkeypatch, _enforce, _meter)

    token = brand_loader.set_workspace_scope("ws-123", frozenset({"magic-unicorn"}))
    try:
        outcome = engine.generate_asset(
            "magic-unicorn",
            "og_image",
            settings=_engine_settings(),
            kits_dir=_real_kits_dir(),
        )
    finally:
        brand_loader.reset_workspace_scope(token)

    assert outcome.result is not None
    assert calls == ["enforce", "meter"], "enforce must precede meter"
    assert seen["enforce_ws"] == "ws-123"
    assert seen["meter_ws"] == "ws-123"
    assert seen["meter_kw"]["cost_cents"] == 4.0
    assert fake.last_user == "ws-123", "workspace forwarded to the backend as user"


def test_engine_quota_denial_stops_before_the_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A QuotaExceeded from the gate aborts generation before any backend call.

    The whole point of the feature: when the gate denies, the (paid) backend
    never runs and nothing is metered. QuotaExceeded subclasses BackendError, so
    the existing REST/MCP handlers map it to a clean 4xx unchanged.
    """
    from brand_ops import brand_loader, engine

    fake = _FakeBackend("litellm", healthy=True)
    monkeypatch.setattr(engine, "load_backend", lambda name, settings: fake)
    monkeypatch.setattr(engine, "save_result", lambda *a, **k: [])

    metered: list[int] = []

    def _deny(ws: str | None) -> None:
        raise QuotaExceeded("over budget")

    _patch_governor(monkeypatch, _deny, lambda *a, **k: metered.append(1))

    token = brand_loader.set_workspace_scope("ws-123", frozenset({"magic-unicorn"}))
    try:
        with pytest.raises(QuotaExceeded):
            engine.generate_asset(
                "magic-unicorn",
                "og_image",
                settings=_engine_settings(),
                kits_dir=_real_kits_dir(),
            )
    finally:
        brand_loader.reset_workspace_scope(token)

    assert fake.generated is False, "the paid backend must not run when denied"
    assert metered == [], "a denied generation must not be metered"
    assert issubclass(QuotaExceeded, BackendError)


def test_engine_dry_run_neither_enforces_nor_meters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dry run (the no-spend preview) touches neither the gate nor the meter."""
    from brand_ops import brand_loader, engine

    touched: list[str] = []
    _patch_governor(
        monkeypatch,
        lambda ws: touched.append("enforce"),
        lambda *a, **k: touched.append("meter"),
    )

    token = brand_loader.set_workspace_scope("ws-123", frozenset({"magic-unicorn"}))
    try:
        outcome = engine.generate_asset(
            "magic-unicorn",
            "og_image",
            dry_run=True,
            settings=_engine_settings(),
            kits_dir=_real_kits_dir(),
        )
    finally:
        brand_loader.reset_workspace_scope(token)

    assert outcome.result is None
    assert touched == [], "dry run is the no-spend path: no gate, no meter"


# ---------------------------------------------------------------------------
# Postgres-backed: the real spend ledger + RLS path.
# SKIPPED unless BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role) on a
# migrated DB -- mirrors tests/test_db_tenancy.py. NOT executed in this build.
# ---------------------------------------------------------------------------

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

_db = pytest.mark.skipif(
    not (_RUN and _URL),
    reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
)


async def _setup_workspace() -> str:
    """Create a fresh workspace (fresh uuid, no teardown needed) and return its id.

    Clears the settings lru_cache first so the engine binds to this run's
    DATABASE_URL even when an earlier (non-DB) test in the file warmed the cache
    before the env was read. Fresh uuids mean runs never collide and need no
    teardown.
    """
    from brand_ops.config import get_settings
    from brand_ops.db.bootstrap import ensure_workspace

    get_settings.cache_clear()
    ws = str(uuid.uuid4())
    await ensure_workspace(ws, f"ws-{ws[:8]}")
    return ws


@_db
@pytest.mark.asyncio
async def test_metering_writes_an_image_spend_event() -> None:
    """meter_image_spend appends one agent_spend_event(kind='image') for the ws.

    Drives the real metering bridge against Postgres under RLS, then reads the
    ledger back under the same scope and asserts exactly one image event landed
    with the estimated cost and the Iris attribution.
    """
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session

    ws = await _setup_workspace()
    try:
        await cost.meter_image_spend(
            ws,
            brand_slug="acme",
            backend="litellm",
            model="gpt-image-1",
            cost_cents=4.0,
        )
        sm = get_sessionmaker()
        since = datetime.now(UTC) - timedelta(days=30)
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                total = await repo.workspace_image_spend_since(session, ws, since)
                assert total == pytest.approx(4.0)
                rows = list(await repo.agent_spend_since(session, ws, since))
                assert len(rows) == 1
                assert rows[0].agent_kind == cost.IMAGE_AGENT_KIND
                assert rows[0].agent_ref == cost.IMAGE_AGENT_REF
    finally:
        await dispose_engine()


@_db
@pytest.mark.asyncio
async def test_under_cap_passes_over_cap_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-workspace image cap: under it the gate passes, at/over it denies.

    Seeds image spend just under a cap (passes), then one more event to reach it
    (the next generation is denied with QuotaExceeded). Proves one tenant can no
    longer drain the shared image budget.
    """
    from brand_ops.db.base import dispose_engine

    ws = await _setup_workspace()
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "100")
    try:
        # 96 cents in the window: under the 100-cent cap -> the gate passes.
        await cost.meter_image_spend(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=96.0
        )
        await cost.enforce_image_quota(ws)  # must not raise

        # One more 4-cent charge brings the window to 100 == the cap -> the next
        # generation is denied.
        await cost.meter_image_spend(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=4.0
        )
        with pytest.raises(QuotaExceeded):
            await cost.enforce_image_quota(ws)
    finally:
        await dispose_engine()


@_db
@pytest.mark.asyncio
async def test_disabled_cap_never_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the cap unset (default), heavy spend never trips the per-ws gate.

    The default-disabled guarantee against a live database: even large recorded
    spend passes when BRAND_OPS_IMAGE_CAP_CENTS is unset and no agent budget is
    set, so existing behavior is unchanged until an operator opts in.
    """
    from brand_ops.db.base import dispose_engine

    ws = await _setup_workspace()
    monkeypatch.delenv("BRAND_OPS_IMAGE_CAP_CENTS", raising=False)
    try:
        await cost.meter_image_spend(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=9999.0
        )
        await cost.enforce_image_quota(ws)  # no cap, no budget -> never denies
    finally:
        await dispose_engine()


@_db
@pytest.mark.asyncio
async def test_agent_weekly_budget_gate_enforced_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The previously record-only weekly_budget_cents now denies when over.

    Sets Iris's weekly budget, records image spend that reaches it, and asserts
    the next generation is denied -- with the image cap itself unset, so this
    isolates the budget gate. Proves governance.weekly_budget_cents is enforced.
    """
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session

    ws = await _setup_workspace()
    monkeypatch.delenv("BRAND_OPS_IMAGE_CAP_CENTS", raising=False)  # isolate the budget gate
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                await repo.upsert_agent_governance(
                    session,
                    ws,
                    agent_kind=cost.IMAGE_AGENT_KIND,
                    agent_ref=cost.IMAGE_AGENT_REF,
                    weekly_budget_cents=50,
                )
        # 50 cents of image spend reaches the 50-cent weekly budget.
        await cost.meter_image_spend(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=50.0
        )
        with pytest.raises(QuotaExceeded):
            await cost.enforce_image_quota(ws)
    finally:
        await dispose_engine()


@_db
@pytest.mark.asyncio
async def test_null_agent_budget_is_no_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A null weekly_budget_cents (the default) never denies -- unchanged behavior.

    No governance row at all (so weekly_budget_cents is null) plus the image cap
    unset: heavy spend still passes. The budget gate is opt-in, exactly like the
    image cap.
    """
    from brand_ops.db.base import dispose_engine

    ws = await _setup_workspace()
    monkeypatch.delenv("BRAND_OPS_IMAGE_CAP_CENTS", raising=False)
    try:
        await cost.meter_image_spend(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=5000.0
        )
        await cost.enforce_image_quota(ws)  # null budget, no cap -> never denies
    finally:
        await dispose_engine()


@_db
@pytest.mark.asyncio
async def test_sync_bridges_write_the_ledger_from_inside_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end, in production's shape: bridge + live loop + real ledger + RLS.

    The tests above drive the async halves directly, and the non-DB tests spy on
    the coroutines. Neither would have caught the defect this file now guards,
    because the money is only really recorded when the SYNC bridge, called from a
    thread that already has a running loop, reaches Postgres. So this asserts the
    whole chain at once: meter through ``meter_image_spend_sync`` while the test's
    own event loop is running, then read the row back and watch the cap that had
    been passing start denying.
    """
    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session

    ws = await _setup_workspace()
    monkeypatch.setenv("BRAND_OPS_IMAGE_CAP_CENTS", "10")
    try:
        # Under the cap (empty ledger): the gate passes, across the bridge.
        cost.enforce_image_quota_sync(ws)

        cost.meter_image_spend_sync(
            ws, brand_slug="acme", backend="litellm", model="gpt-image-1", cost_cents=12.0
        )

        sm = get_sessionmaker()
        since = datetime.now(UTC) - timedelta(days=30)
        async with sm() as session:
            async with scoped_session(session, workspace_id=ws):
                # agent_spend_since groups by (agent_kind, agent_ref), so one row
                # is one agent and ``calls`` is the event count within it.
                rows = list(await repo.agent_spend_since(session, ws, since))
                assert len(rows) == 1, "the bridged meter wrote no ledger row"
                assert rows[0].agent_kind == cost.IMAGE_AGENT_KIND
                assert rows[0].agent_ref == cost.IMAGE_AGENT_REF
                assert rows[0].calls == 1
                # kind='image' specifically, which is what the cap reads.
                total = await repo.workspace_image_spend_since(session, ws, since)
                assert total == pytest.approx(12.0)

        # And the recorded spend is what the NEXT request's gate reads: 12 cents
        # against a 10-cent cap now denies. Metering that does not feed the gate
        # is just bookkeeping.
        with pytest.raises(QuotaExceeded):
            cost.enforce_image_quota_sync(ws)
    finally:
        await dispose_engine()
