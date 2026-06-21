"""workspace_member: a narrow, opt-in SELECT policy for the admin export

The GDPR/offboarding export (``repository.export_workspace_rows``) dumps every DB
row a workspace owns so an export is the inverse of a delete. It runs under the
workspace's scope (``app.current_workspace``), which is right for the data tables
but wrong for ``workspace_member``: since 0002 that table is USER-scoped
(``user_key = app.current_user_key``) so the switcher can list a user's own
memberships and so a member cannot enumerate co-members. Under workspace-only
scope the user-scoped policy therefore matches nothing, and the export silently
dropped the membership graph.

This adds a SECOND, SELECT-only policy that reveals a workspace's members ONLY
when the caller has BOTH scoped to that workspace AND explicitly opted in by
setting ``app.membership_export='on'`` for the transaction. RLS permissive
policies are OR'd, so the existing user-scoped ``workspace_member_self`` policy is
unchanged: a normal request never sets the export GUC, so a member still sees only
their own row and cannot enumerate co-members -- the 0002 posture holds. Only the
admin-gated export path sets the GUC (in the same transaction, LOCAL), so only it
gains the workspace-wide read. Writes are untouched (this policy is FOR SELECT, so
the self policy's WITH CHECK still governs inserts/updates).

Additive and reversible; touches only the one table's read policy.

Revision ID: 0005_membership_export_read
Revises: 0004_asset_catalog_sot
Create Date: 2026-06-20 00:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_membership_export_read"
down_revision: str | None = "0004_asset_catalog_sot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Both GUC reads are NULLIF-hardened (an unset OR empty setting becomes NULL), the
# same deny-by-default shape as every other policy here: an empty workspace yields
# no rows rather than a cast error, and an unset opt-in flag fails the AND.
_WS = "NULLIF(current_setting('app.current_workspace', true), '')::uuid"
_EXPORT = "NULLIF(current_setting('app.membership_export', true), '')"


def upgrade() -> None:
    op.execute(
        "CREATE POLICY workspace_member_admin_export ON workspace_member "
        "FOR SELECT "
        f"USING (workspace_id = {_WS} AND {_EXPORT} = 'on')"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS workspace_member_admin_export ON workspace_member"
    )
