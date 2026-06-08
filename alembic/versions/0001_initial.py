"""initial brand-ops schema with row level security

Creates every table, then turns on row level security for the
workspace-scoped tables and adds a policy that filters each row by
``current_setting('app.current_workspace')::uuid``. The application sets that
value per transaction via brand_ops.db.base.workspace_session.

Two database roles back this design (create them out of band, once per cluster):

  brand_ops_owner
      Owns the schema and runs these migrations. Member of brand_ops_app so it
      can read/write data during setup and tests.

      CREATE ROLE brand_ops_owner LOGIN PASSWORD '...';

  brand_ops_app
      The runtime role the FastAPI/MCP/agent processes connect as. Created
      NOBYPASSRLS so it can never see across workspaces, even if a query forgets
      its workspace filter. Granted CRUD on the tables but not ownership.

      CREATE ROLE brand_ops_app LOGIN PASSWORD '...' NOBYPASSRLS;
      GRANT brand_ops_app TO brand_ops_owner;
      -- after this migration:
      GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
          TO brand_ops_app;

Table owners (brand_ops_owner) are exempt from RLS by default, which is why the
runtime role must NOT be the owner.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-08 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables guarded by row level security. Each filters rows by the per-transaction
# app.current_workspace setting.
_WORKSPACE_SCOPED_TABLES: tuple[str, ...] = (
    "workspace_member",
    "brand",
    "asset",
    "generation_job",
    "brand_research_run",
)


def upgrade() -> None:
    # gen_random_uuid() lives in pgcrypto.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "workspace",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "workspace_member",
        sa.Column("user_key", sa.String(length=255), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspace.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_key", "workspace_id"),
    )

    op.create_table(
        "brand",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("parent_slug", sa.String(length=120), nullable=True),
        sa.Column("tagline", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
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
        sa.UniqueConstraint(
            "workspace_id", "slug", name="uq_brand_workspace_slug"
        ),
    )
    op.create_index("ix_brand_workspace_id", "brand", ["workspace_id"])

    op.create_table(
        "asset",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_slug", sa.String(length=120), nullable=False),
        sa.Column("asset_type", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=120), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("backend", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("mime", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("cost_cents", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("prompt", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
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
    op.create_index("ix_asset_workspace_id", "asset", ["workspace_id"])
    op.create_index("ix_asset_brand_slug", "asset", ["brand_slug"])

    op.create_table(
        "asset_version",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id", "version_no", name="uq_asset_version_no"
        ),
    )
    op.create_index("ix_asset_version_asset_id", "asset_version", ["asset_id"])

    op.create_table(
        "generation_job",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
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
    )
    op.create_index(
        "ix_generation_job_workspace_id", "generation_job", ["workspace_id"]
    )
    op.create_index("ix_generation_job_status", "generation_job", ["status"])

    op.create_table(
        "brand_research_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_company", sa.String(length=255), nullable=True),
        sa.Column("target_domain", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
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
        "ix_brand_research_run_workspace_id",
        "brand_research_run",
        ["workspace_id"],
    )
    op.create_index(
        "ix_brand_research_run_status", "brand_research_run", ["status"]
    )

    # Row level security. FORCE so even a table owner is constrained, then a
    # single permissive policy per table that scopes every command to the
    # workspace set on the current transaction. Missing/empty setting (the
    # third arg true returns NULL instead of erroring) yields no rows.
    for table in _WORKSPACE_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            "USING (workspace_id = "
            "current_setting('app.current_workspace', true)::uuid) "
            "WITH CHECK (workspace_id = "
            "current_setting('app.current_workspace', true)::uuid)"
        )


def downgrade() -> None:
    for table in _WORKSPACE_SCOPED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_brand_research_run_status", table_name="brand_research_run")
    op.drop_index(
        "ix_brand_research_run_workspace_id", table_name="brand_research_run"
    )
    op.drop_table("brand_research_run")

    op.drop_index("ix_generation_job_status", table_name="generation_job")
    op.drop_index(
        "ix_generation_job_workspace_id", table_name="generation_job"
    )
    op.drop_table("generation_job")

    op.drop_index("ix_asset_version_asset_id", table_name="asset_version")
    op.drop_table("asset_version")

    op.drop_index("ix_asset_brand_slug", table_name="asset")
    op.drop_index("ix_asset_workspace_id", table_name="asset")
    op.drop_table("asset")

    op.drop_index("ix_brand_workspace_id", table_name="brand")
    op.drop_table("brand")

    op.drop_table("workspace_member")
    op.drop_table("workspace")
