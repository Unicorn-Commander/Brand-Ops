"""Tests for the agent governance + spend vertical.

Two layers:

  1. Pure unit tests (always run, no Postgres, no network): the cost estimator,
     the default standing orders, and the PUT route's validation logic (a small
     pure validator in ``brand_ops.governance``).

  2. A Postgres-backed RLS proof for the two new tables (agent_governance,
     agent_spend_event). SKIPPED unless ``BRAND_OPS_RUN_DB_TESTS=1`` and
     ``DATABASE_URL`` point at the ``brand_ops_app`` (NOBYPASSRLS) role on a
     migrated database, exactly like ``tests/test_db_tenancy.py``. This file does
     not run it in CI; the parent runs the full suite.
"""
from __future__ import annotations

import os
import uuid

import pytest

from brand_ops import governance

# ---------------------------------------------------------------------------
# estimate_cost_cents
# ---------------------------------------------------------------------------


def test_estimate_cost_none_when_no_usage() -> None:
    # No usage at all, or a usage dict with no token totals -> no cost, never a
    # fabricated number.
    assert governance.estimate_cost_cents("claude-opus", None) is None
    assert governance.estimate_cost_cents("claude-opus", {}) is None
    assert governance.estimate_cost_cents("claude-opus", {"foo": "bar"}) is None
    assert governance.estimate_cost_cents("claude-opus", "not-a-dict") is None


def test_estimate_cost_local_models_are_near_zero() -> None:
    # Local / self-hosted models (qwen and friends) price at ~0: they are
    # billing-exempt to run, so a metered turn on them costs essentially nothing.
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}
    assert governance.estimate_cost_cents("qwen3.6-35b-a3b", usage) == 0.0
    assert governance.estimate_cost_cents("qwen3.6-vision", usage) == 0.0


def test_estimate_cost_frontier_higher_than_mid_higher_than_local() -> None:
    # The ordering that matters for the product: an Opus-class turn costs more
    # than a GPT-mid turn, which costs more than a free local turn, for identical
    # token counts.
    usage = {"total_tokens": 10_000}
    opus = governance.estimate_cost_cents("claude-opus-4", usage)
    gpt = governance.estimate_cost_cents("gpt-4o", usage)
    local = governance.estimate_cost_cents("qwen3.6-35b", usage)
    assert opus is not None and gpt is not None and local is not None
    assert opus > gpt > local
    assert local == 0.0


def test_estimate_cost_uses_total_tokens_scaling() -> None:
    # The estimate scales linearly with total tokens at the model's per-1k rate.
    one_k = governance.estimate_cost_cents("gpt-4o", {"total_tokens": 1000})
    ten_k = governance.estimate_cost_cents("gpt-4o", {"total_tokens": 10_000})
    assert one_k is not None and ten_k is not None
    assert round(ten_k, 4) == round(one_k * 10, 4)


def test_estimate_cost_falls_back_to_parts_when_no_total() -> None:
    # When only prompt/completion are present, they sum to the total used.
    parts = governance.estimate_cost_cents(
        "gpt-4o", {"prompt_tokens": 400, "completion_tokens": 600}
    )
    total = governance.estimate_cost_cents("gpt-4o", {"total_tokens": 1000})
    assert parts == total


def test_estimate_cost_unknown_model_is_conservative_nonzero() -> None:
    # An unknown model is never silently costed at zero (which would hide spend);
    # it gets the conservative mid-tier default.
    cost = governance.estimate_cost_cents("some-new-model-xyz", {"total_tokens": 1000})
    assert cost is not None and cost > 0.0


def test_estimate_cost_ignores_bool_and_bad_token_values() -> None:
    # bool is an int subclass; it must not be treated as a token count, and a
    # non-numeric value must not crash the estimate.
    assert governance.estimate_cost_cents("gpt-4o", {"total_tokens": True}) is None
    assert (
        governance.estimate_cost_cents("gpt-4o", {"total_tokens": "not-a-number"})
        is None
    )


# ---------------------------------------------------------------------------
# default_standing_orders
# ---------------------------------------------------------------------------


def test_default_standing_orders_director_matches_seed_copy() -> None:
    # The Director's seed orders are the three lines the Crew page showed as
    # static text (minus the TODO prefix), so the page reads identically before
    # the first edit.
    text = governance.default_standing_orders("director", "iris")
    assert "Research before proposing. Cite the dossier." in text
    assert "Three directions minimum, one recommendation, defend it." in text
    assert "Spawn a steward at every ratify; set its standing orders." in text
    # No leftover stub markers leak into the seed copy.
    assert "TODO" not in text


def test_default_standing_orders_steward_is_per_brand_guard_line() -> None:
    text = governance.default_standing_orders("steward", "magic-unicorn")
    assert "palette" in text.lower()
    assert "drift" in text.lower()
    assert "TODO" not in text


def test_default_standing_orders_specialist_names_the_agent() -> None:
    text = governance.default_standing_orders("specialist", "asset_pack")
    assert "asset_pack" in text
    assert "TODO" not in text


def test_default_standing_orders_unknown_kind_is_honest_default() -> None:
    text = governance.default_standing_orders("identity", "brand/some-agent")
    assert text  # non-empty
    assert "TODO" not in text


# ---------------------------------------------------------------------------
# validate_governance_update (the PUT route's validation logic)
# ---------------------------------------------------------------------------


def test_validate_accepts_known_kind_level_and_budget() -> None:
    assert (
        governance.validate_governance_update(
            agent_kind="director", autonomy="autonomous", weekly_budget_cents=5000
        )
        is None
    )
    # None autonomy / budget means "not changing it" -> valid.
    assert (
        governance.validate_governance_update(
            agent_kind="steward", autonomy=None, weekly_budget_cents=None
        )
        is None
    )
    # Zero budget is a valid cap (stop immediately), not an error.
    assert (
        governance.validate_governance_update(
            agent_kind="specialist", autonomy="propose", weekly_budget_cents=0
        )
        is None
    )


def test_validate_rejects_unknown_kind() -> None:
    err = governance.validate_governance_update(
        agent_kind="wizard", autonomy="propose", weekly_budget_cents=None
    )
    assert err is not None and "agent_kind" in err


def test_validate_rejects_unknown_autonomy() -> None:
    err = governance.validate_governance_update(
        agent_kind="director", autonomy="full-send", weekly_budget_cents=None
    )
    assert err is not None and "autonomy" in err


def test_validate_rejects_negative_budget() -> None:
    err = governance.validate_governance_update(
        agent_kind="director", autonomy="propose", weekly_budget_cents=-1
    )
    assert err is not None and "weekly_budget_cents" in err


def test_vocabulary_constants_are_stable() -> None:
    # The wire contract depends on these exact strings; lock them.
    assert governance.AUTONOMY_LEVELS == ("propose", "auto_with_review", "autonomous")
    assert governance.AGENT_KINDS == ("director", "steward", "specialist", "identity")
    assert governance.DEFAULT_AUTONOMY == "propose"


# ---------------------------------------------------------------------------
# Postgres-backed RLS proof for the two new tables.
# SKIPPED unless BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role) on
# a migrated DB -- mirrors tests/test_db_tenancy.py. NOT executed in this build.
# ---------------------------------------------------------------------------

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

_db = pytest.mark.skipif(
    not (_RUN and _URL),
    reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
)


@_db
@pytest.mark.asyncio
async def test_governance_rls_isolates_between_workspaces() -> None:
    """A governance row + a spend event in ws1 are invisible from ws2 (RLS).

    Proves the migration 0003 policies hold for the runtime NOBYPASSRLS role:
    writing under ws1's scope and reading under ws2's scope returns nothing, and
    reading back under ws1 returns the row. Uses fresh uuids so runs never
    collide and need no teardown.
    """
    from datetime import UTC, datetime, timedelta

    from brand_ops.db import repository as repo
    from brand_ops.db.base import dispose_engine, get_sessionmaker, scoped_session
    from brand_ops.db.bootstrap import ensure_membership, ensure_workspace

    ws1, ws2 = str(uuid.uuid4()), str(uuid.uuid4())
    user = f"user-{uuid.uuid4()}"
    try:
        await ensure_workspace(ws1, f"ws-{ws1[:8]}")
        await ensure_workspace(ws2, f"ws-{ws2[:8]}")
        await ensure_membership(
            user_key=user, workspace_id=ws1, role="owner", is_default=True
        )

        sm = get_sessionmaker()
        async with sm() as session:
            # Write a governance row + a spend event under ws1's scope.
            async with scoped_session(session, workspace_id=ws1):
                await repo.upsert_agent_governance(
                    session,
                    ws1,
                    agent_kind="director",
                    agent_ref="iris",
                    autonomy="autonomous",
                    standing_orders="Ship it.",
                    weekly_budget_cents=12_345,
                    model="claude-opus-4",
                    updated_by=user,
                )
                await repo.record_spend_event(
                    session,
                    ws1,
                    agent_kind="director",
                    agent_ref="iris",
                    kind="chat",
                    model="claude-opus-4",
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                    cost_cents=0.675,
                )

            # ws1 sees its own data.
            async with scoped_session(session, workspace_id=ws1):
                rows = list(await repo.list_agent_governance(session, ws1))
                assert len(rows) == 1
                assert rows[0].autonomy == "autonomous"
                assert rows[0].weekly_budget_cents == 12_345
                since = datetime.now(UTC) - timedelta(days=7)
                spend = list(await repo.agent_spend_since(session, ws1, since))
                assert len(spend) == 1
                assert int(spend[0].total_tokens) == 150

            # ws2 sees NONE of ws1's governance or spend (RLS isolation).
            async with scoped_session(session, workspace_id=ws2):
                assert list(await repo.list_agent_governance(session, ws2)) == []
                since = datetime.now(UTC) - timedelta(days=7)
                assert list(await repo.agent_spend_since(session, ws2, since)) == []
    finally:
        await dispose_engine()
