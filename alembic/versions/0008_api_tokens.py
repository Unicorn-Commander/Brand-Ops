"""api_token: the durable Brand-Ops credential (personal access token)

Brand-Ops accepted only short-lived IdP JWTs, so a headless MCP client -- the
stdio transport above all -- had nothing it could hold and ran with no identity.
This table is the missing durable credential: a ``brand_pat_``-prefixed secret
stored ONLY as its sha256 hash, owned by a uc_uid, optionally bound to one
workspace and/or one brand, with an expiry and a revocation stamp.

Deliberately NOT row-level-security scoped, unlike every workspace-scoped table:
a verification looks the row up by its sha256 hash BEFORE any identity exists, so
there is no ``app.current_user_key`` / ``app.current_workspace`` GUC set at that
moment and an RLS policy would simply make every lookup return zero rows. The row
is reachable only by presenting the secret whose hash it is (a 256-bit
unguessable value); every management read filters on ``user_key`` in the query
itself. The token carries no grants of its own -- it authenticates AS its owner
and is re-resolved through the RLS-protected ``workspace_member`` table on every
use -- so the tenant boundary still lives entirely behind RLS.

Additive and reversible; touches only this new table. The runtime role picks it
up under the same ``GRANT ... ON ALL TABLES`` the operator runs after a migration.

Revision ID: 0008_api_tokens
Revises: 0007_asset_soft_delete
Create Date: 2026-07-30 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_api_tokens"
down_revision: str | None = "0007_asset_soft_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_key", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        # sha256 hex of the raw token. The plaintext is never stored.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        # Display-only, non-secret fragment so a human can identify a row.
        sa.Column("token_prefix", sa.String(length=32), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("brand_slug", sa.String(length=120), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_api_token_hash"),
    )
    op.create_index("ix_api_token_user_key", "api_token", ["user_key"])
    op.create_index("ix_api_token_workspace_id", "api_token", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_api_token_workspace_id", table_name="api_token")
    op.drop_index("ix_api_token_user_key", table_name="api_token")
    op.drop_table("api_token")
