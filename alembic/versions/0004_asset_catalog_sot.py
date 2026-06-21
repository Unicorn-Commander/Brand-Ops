"""asset catalog source-of-truth: the columns + key that let Postgres hold the
catalog the on-disk sidecars currently hold.

Brand-Ops has always written a ``<file>.json`` sidecar beside every generated or
uploaded image and read the catalog back by walking those sidecars
(``api/catalog.read_assets``). The ``asset`` table existed but was never written
to. This migration adds the columns the sidecar carries that the table lacked --
``kind``, ``transparent``, ``reference_images``, ``lifecycle_status``,
``compliance``, and the sidecar's ``created_at`` string label (``created_label``)
-- so a row reconstructs the exact ``AssetRecord`` the read path returns, and adds
the partial-unique index on ``(workspace_id, storage_key)`` that makes the
dual-write and the one-time backfill idempotent (upsert on the file's
workspace-relative path).

Everything is additive, nullable, and reversible. No data is moved here: the
dual-write (new generations) and the backfill (existing files) populate the rows;
reads stay on the sidecars until an operator flips ``BRAND_OPS_CATALOG_SOT=db``.
The ``asset`` table already has its workspace-isolation RLS policy (0001), so no
policy change is needed; the new index is workspace-scoped by the leading
``workspace_id`` column.

Revision ID: 0004_asset_catalog_sot
Revises: 0003_agent_governance_and_spend
Create Date: 2026-06-20 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_asset_catalog_sot"
down_revision: str | None = "0003_agent_governance_and_spend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The fields the sidecar records but the table did not. All nullable, so the
    # add is instant (no table rewrite) and pre-existing rows stay valid.
    op.add_column("asset", sa.Column("kind", sa.String(length=32), nullable=True))
    op.add_column("asset", sa.Column("transparent", sa.Boolean(), nullable=True))
    op.add_column("asset", sa.Column("reference_images", sa.JSON(), nullable=True))
    op.add_column(
        "asset", sa.Column("lifecycle_status", sa.String(length=32), nullable=True)
    )
    op.add_column("asset", sa.Column("compliance", sa.JSON(), nullable=True))
    op.add_column(
        "asset", sa.Column("created_label", sa.String(length=64), nullable=True)
    )

    # The idempotency key for the dual-write and the backfill: one row per stored
    # file within a workspace. Partial (only where storage_key is set) so the unique
    # constraint never trips on the keyless pre-SoR rows, and small. ON CONFLICT in
    # ``repository.upsert_asset`` targets exactly these columns.
    op.create_index(
        "uq_asset_ws_storage_key",
        "asset",
        ["workspace_id", "storage_key"],
        unique=True,
        postgresql_where=sa.text("storage_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_asset_ws_storage_key", table_name="asset")
    op.drop_column("asset", "created_label")
    op.drop_column("asset", "compliance")
    op.drop_column("asset", "lifecycle_status")
    op.drop_column("asset", "reference_images")
    op.drop_column("asset", "transparent")
    op.drop_column("asset", "kind")
