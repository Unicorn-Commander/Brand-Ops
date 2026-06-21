"""idempotency_key: a workspace-scoped, RLS-forced claim table for retried POSTs

A client that retries a cost-incurring POST (image generation) with the same
``Idempotency-Key`` header must get the original result, not a second generation
and a second charge. This table is the durable, cross-worker claim: one row per
(workspace, key), unique so the claim is atomic via INSERT ... ON CONFLICT DO
NOTHING. It carries the same workspace-scoped row level security as the rest of
Brand-Ops (RLS enabled and FORCED, policy on the hardened ``app.current_workspace``
GUC so an unset/empty setting yields zero rows -- deny-by-default).

Additive and reversible; touches only this new table. The runtime role picks it up
under the same ``GRANT ... ON ALL TABLES`` the operator runs after a migration.

Revision ID: 0006_idempotency_keys
Revises: 0005_membership_export_read
Create Date: 2026-06-21 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_idempotency_keys"
down_revision: str | None = "0005_membership_export_read"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hardened GUC read, identical to 0002/0003: unset OR empty becomes NULL, so the
# equality is NULL and the row is invisible (deny-by-default, never a cast error).
_WS = "NULLIF(current_setting('app.current_workspace', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "idempotency_key",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idem_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'in_progress'"),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idem_key", name="uq_idempotency_ws_key"),
    )
    op.create_index(
        "ix_idempotency_key_workspace_id", "idempotency_key", ["workspace_id"]
    )
    # Indexed for the time-based sweep that prunes old claims.
    op.create_index(
        "ix_idempotency_key_created_at", "idempotency_key", ["created_at"]
    )

    op.execute("ALTER TABLE idempotency_key ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_key FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY idempotency_key_workspace_isolation ON idempotency_key "
        f"USING (workspace_id = {_WS}) "
        f"WITH CHECK (workspace_id = {_WS})"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS idempotency_key_workspace_isolation ON idempotency_key"
    )
    op.execute("ALTER TABLE idempotency_key NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE idempotency_key DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_idempotency_key_created_at", table_name="idempotency_key")
    op.drop_index("ix_idempotency_key_workspace_id", table_name="idempotency_key")
    op.drop_table("idempotency_key")
