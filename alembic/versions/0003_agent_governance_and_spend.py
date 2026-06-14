"""agent governance + spend metering: two workspace-scoped, RLS-forced tables

This adds the persistence behind the Crew page's agent-governance controls (an
autonomy posture, standing orders, an optional weekly budget, and a model
override per agent) plus an append-only spend ledger the page reads to show real
7-day spend per agent. Both tables are workspace-scoped and carry the same row
level security shape as the rest of Brand-Ops: RLS enabled and FORCED, with a
policy filtering on the hardened ``app.current_workspace`` GUC so an unset OR
empty setting degrades to NULL -> zero rows (deny-by-default, never a cast
error). The pattern mirrors 0002 exactly (see ``_WS``).

``agent_governance`` holds at most one row per (workspace, agent_kind,
agent_ref); a missing row means "the agent's defaults", which the API
synthesizes so the page always renders. ``agent_spend_event`` is the raw
metering ledger: one row per metered agent turn (text chat in v1), carrying the
gateway-reported token usage and an ESTIMATED cost, indexed for the per-agent
7-day rollup.

Both changes are additive and reversible and touch only Brand-Ops tables. The
runtime role grants are the same ``GRANT ... ON ALL TABLES`` the operator runs
after a migration (these new tables are picked up by that grant); RLS keeps the
NOBYPASSRLS app role inside one workspace per transaction regardless.

Revision ID: 0003_agent_governance_and_spend
Revises: 0002_membership_user_scoped_rls
Create Date: 2026-06-14 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_agent_governance_and_spend"
down_revision: str | None = "0002_membership_user_scoped_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hardened GUC read, identical to 0002: an unset OR empty setting becomes NULL,
# which makes the equality NULL and the row invisible -- deny-by-default, never a
# cast error under connection pooling.
_WS = "NULLIF(current_setting('app.current_workspace', true), '')::uuid"

# The two new tables get the same workspace-scoped policy as the data tables.
_WORKSPACE_SCOPED: tuple[str, ...] = (
    "agent_governance",
    "agent_spend_event",
)


def _enable_rls(table: str) -> None:
    """Enable + FORCE RLS and create the workspace-isolation policy on ``table``.

    FORCE so the boundary holds for every role (the runtime role is NOBYPASSRLS
    already; FORCE also subjects the owner), and the policy reads the hardened
    GUC so a missing/empty workspace yields no rows. Mirrors the create side of
    0001/0002.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_workspace_isolation ON {table} "
        f"USING (workspace_id = {_WS}) "
        f"WITH CHECK (workspace_id = {_WS})"
    )


def upgrade() -> None:
    # --- agent_governance: one row per (workspace, agent_kind, agent_ref) ----
    op.create_table(
        "agent_governance",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_kind", sa.String(length=32), nullable=False),
        sa.Column("agent_ref", sa.String(length=255), nullable=False),
        sa.Column(
            "autonomy",
            sa.String(length=32),
            server_default=sa.text("'propose'"),
            nullable=False,
        ),
        sa.Column("standing_orders", sa.Text(), nullable=True),
        sa.Column("weekly_budget_cents", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspace.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_kind",
            "agent_ref",
            name="uq_agent_governance_ws_kind_ref",
        ),
    )
    op.create_index(
        "ix_agent_governance_workspace_id", "agent_governance", ["workspace_id"]
    )

    # --- agent_spend_event: the append-only per-turn metering ledger ---------
    op.create_table(
        "agent_spend_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_kind", sa.String(length=32), nullable=False),
        sa.Column("agent_ref", sa.String(length=255), nullable=False),
        sa.Column("brand_slug", sa.String(length=120), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspace.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_spend_event_workspace_id", "agent_spend_event", ["workspace_id"]
    )
    op.create_index(
        "ix_agent_spend_event_created_at", "agent_spend_event", ["created_at"]
    )
    op.create_index(
        "ix_agent_spend_event_ws_kind_ref",
        "agent_spend_event",
        ["workspace_id", "agent_kind", "agent_ref"],
    )

    # RLS last, once both tables exist.
    for table in _WORKSPACE_SCOPED:
        _enable_rls(table)


def downgrade() -> None:
    # Reverse order: policies, FORCE/RLS, indexes, then the tables themselves.
    for table in reversed(_WORKSPACE_SCOPED):
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_agent_spend_event_ws_kind_ref", table_name="agent_spend_event")
    op.drop_index("ix_agent_spend_event_created_at", table_name="agent_spend_event")
    op.drop_index("ix_agent_spend_event_workspace_id", table_name="agent_spend_event")
    op.drop_table("agent_spend_event")

    op.drop_index("ix_agent_governance_workspace_id", table_name="agent_governance")
    op.drop_table("agent_governance")
