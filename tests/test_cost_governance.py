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

import os
import uuid
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

    monkeypatch.setattr(engine.cost, "enforce_image_quota_sync", _enforce)
    monkeypatch.setattr(engine.cost, "meter_image_spend_sync", _meter)

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

    monkeypatch.setattr(engine.cost, "enforce_image_quota_sync", _deny)
    monkeypatch.setattr(
        engine.cost, "meter_image_spend_sync", lambda *a, **k: metered.append(1)
    )

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
    monkeypatch.setattr(
        engine.cost, "enforce_image_quota_sync", lambda ws: touched.append("enforce")
    )
    monkeypatch.setattr(
        engine.cost, "meter_image_spend_sync", lambda *a, **k: touched.append("meter")
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
