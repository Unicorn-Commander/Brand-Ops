"""workspace_member RLS user-scoped + empty-GUC hardening

0001 scoped EVERY workspace table (workspace_member included) by
``app.current_workspace``. That makes membership ENUMERATION impossible for the
NOBYPASSRLS runtime role: "which workspaces does this user belong to" spans
workspaces, but the workspace-scoped policy only ever reveals rows for the
single workspace set on the transaction (and none when none is set). The
workspace switcher and the default-workspace resolution both have to read a
user's memberships BEFORE any workspace is chosen, so workspace_member must be
scoped by the acting USER, not by a workspace.

This repoints workspace_member's policy at ``app.current_user_key`` (the acting
uchub sub) and hardens every policy's GUC read with ``NULLIF(..., '')`` so an
empty-string setting degrades to NULL -> zero rows (the intended
deny-by-default) instead of erroring on ``''::uuid``. ``current_setting(name,
true)`` only returns NULL for an UNSET key, not for one set to ''; under
connection pooling a reset-to-empty GUC would otherwise crash the deny path.

Both changes are additive/reversible and touch only Brand-Ops tables.

Revision ID: 0002_membership_user_scoped_rls
Revises: 0001_initial
Create Date: 2026-06-13 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_membership_user_scoped_rls"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Hardened GUC reads: an unset OR empty setting becomes NULL, which makes the
# equality NULL and the row invisible -- deny-by-default, never a cast error.
_WS = "NULLIF(current_setting('app.current_workspace', true), '')::uuid"
_UK = "NULLIF(current_setting('app.current_user_key', true), '')"

# The data tables stay workspace-scoped; only their GUC read is hardened.
_WORKSPACE_SCOPED: tuple[str, ...] = (
    "asset",
    "brand",
    "brand_research_run",
    "generation_job",
)


def upgrade() -> None:
    for table in _WORKSPACE_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            f"USING (workspace_id = {_WS}) "
            f"WITH CHECK (workspace_id = {_WS})"
        )

    # workspace_member becomes user-scoped: a caller reads/writes only their own
    # membership rows, so the switcher can list a user's workspaces before one is
    # active, and seeding a row for a user only requires app.current_user_key set
    # to that user.
    op.execute(
        "DROP POLICY IF EXISTS workspace_member_workspace_isolation ON workspace_member"
    )
    op.execute(
        "CREATE POLICY workspace_member_self ON workspace_member "
        f"USING (user_key = {_UK}) "
        f"WITH CHECK (user_key = {_UK})"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS workspace_member_self ON workspace_member")
    op.execute(
        "CREATE POLICY workspace_member_workspace_isolation ON workspace_member "
        "USING (workspace_id = current_setting('app.current_workspace', true)::uuid) "
        "WITH CHECK (workspace_id = current_setting('app.current_workspace', true)::uuid)"
    )
    for table in _WORKSPACE_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_workspace_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_workspace_isolation ON {table} "
            "USING (workspace_id = current_setting('app.current_workspace', true)::uuid) "
            "WITH CHECK (workspace_id = current_setting('app.current_workspace', true)::uuid)"
        )
