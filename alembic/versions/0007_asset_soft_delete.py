"""asset.deleted_at: soft-delete tombstone + retention purge support

Closes the data-retention audit gap: until now there was no way to delete a
single catalog asset and no time-based purge, so removed data would linger
forever. This adds a nullable ``deleted_at`` column to ``asset``:

* A soft-delete (the recoverable delete) stamps ``deleted_at`` with the delete
  instant. The catalog read path filters ``deleted_at IS NULL`` (see
  ``repository.list_assets``), so a soft-deleted asset disappears from the
  Library while its bytes remain on disk -- it can be recovered until purged.
* The retention sweeper (``brand_ops.retention_sweep``) hard-deletes the on-disk
  file, its sidecar, and the row once ``deleted_at`` is older than the
  configurable ``BRAND_OPS_RETENTION_DAYS`` window (0 = disabled, the safe
  default that never purges).

Additive and reversible; touches only this one nullable column. No RLS change:
``asset`` already carries its workspace-scoped policy (migration 0003), and a
new column inherits it -- a soft-deleted (or any) row is still only ever visible
to its own tenant. The runtime role picks the column up under the same ``GRANT
... ON ALL TABLES`` the operator runs after a migration.

Revision ID: 0007_asset_soft_delete
Revises: 0006_idempotency_keys
Create Date: 2026-06-21 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_asset_soft_delete"
down_revision: str | None = "0006_idempotency_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "deleted_at")
