"""Admin API: runtime tenant provisioning, offboarding, export, usage.

A small, deliberately separate router for the operations a suite operator (not a
tenant) performs: standing a workspace up at runtime (so onboarding no longer
needs an env allow-list edit and a redeploy), granting and revoking membership,
offboarding a tenant (delete its DB rows via the FK CASCADE plus purge its
on-disk brand-kits subtree), exporting a tenant's data as a zip, and a
cross-tenant usage view. Every route here is gated by :func:`require_admin`, on
top of the normal authenticated principal, and every action is audited to a
dedicated logger.

Authorization is a closed allow-list. ``BRAND_OPS_ADMIN_SUBS`` is a
comma-separated list of uc_uid subjects (the same spirit as the membership
``BRAND_OPS_MEMBER_ALLOWLIST``); a caller whose uc_uid is not in it gets a 403.
When the variable is unset or empty, NO ONE is admin (deny-by-default): there is
no implicit admin, so a deploy that forgets to configure it is locked down, not
wide open. The anonymous principal (AUTH_REQUIRED off, local/dev) is never admin.

This module reads its own env directly (``os.getenv``) rather than extending the
shared Settings model, matching how api/auth.py owns its AUTH_REQUIRED knob. It
imports ``require_principal`` from api/auth.py but does not modify it.

The DB writes here cross the per-workspace boundary that the rest of the app is
scoped within, and are commented at each crossing: deleting a workspace and the
cross-tenant usage read necessarily act outside any single workspace. They are
safe because they are admin-gated and because the ``workspace`` anchor table is
not row-level-secured (the per-tenant counts in the usage view are still read
under each workspace's own RLS scope, one workspace at a time).
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from .. import admin_ops
from ..db import repository as repo
from ..db.base import get_sessionmaker, scoped_session
from ..db.tenancy import coerce_uuid
from .auth import Principal, require_principal

_log = logging.getLogger("brand_ops.api.admin")
# A dedicated audit logger so admin actions can be routed/retained separately
# from ordinary request logs. Every mutating route emits one line here naming the
# actor (uc_uid), the action, and the target.
_audit = logging.getLogger("brand_ops.admin.audit")

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Admin authorization (closed allow-list, deny-by-default)
# ---------------------------------------------------------------------------


def admin_subs() -> frozenset[str]:
    """The set of uc_uid subjects allowed to use the admin API.

    Parsed from ``BRAND_OPS_ADMIN_SUBS`` (comma-separated) at call time, so a
    deploy can rotate the list without a code change and a test can set it with
    an env var. Empty (unset or all-blank) means no admins at all: the gate then
    denies everyone, which is the safe default for an operations API.
    """
    raw = os.getenv("BRAND_OPS_ADMIN_SUBS", "")
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def require_admin(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    """Require an authenticated caller whose uc_uid is in the admin allow-list.

    Runs after ``require_principal`` (so the caller is already validated and
    workspace-resolved), then enforces two things: the caller must not be
    anonymous (AUTH_REQUIRED off yields an anonymous principal, which is never an
    admin), and their uc_uid must be a member of :func:`admin_subs`. Anything else
    is a 403. With ``BRAND_OPS_ADMIN_SUBS`` unset the allow-list is empty, so the
    check fails closed and the whole admin surface is unreachable until an
    operator configures it. A denied attempt is audited.
    """
    allow = admin_subs()
    if principal.anonymous or principal.uc_uid not in allow:
        _audit.warning(
            "admin access DENIED for uc_uid=%s (issuer=%s, anonymous=%s)",
            principal.uc_uid,
            principal.issuer,
            principal.anonymous,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin access required"
        )
    return principal


_Admin = Annotated[Principal, Depends(require_admin)]


def _require_db() -> None:
    """Raise a clean 503 when no database is configured for an admin DB action.

    The admin provisioning/offboarding routes are meaningless without the tenancy
    DB, so rather than degrade silently (the read path's deny-by-default posture)
    they tell the operator the dependency is missing.
    """
    sm_url = os.getenv("DATABASE_URL", "").strip()
    if not sm_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database is not configured (DATABASE_URL unset)",
        )


def _valid_ws_id(workspace_id: str) -> str:
    """Return the canonical uuid string for a workspace id, or 422 if malformed."""
    canonical = coerce_uuid(workspace_id)
    if canonical is None:
        raise HTTPException(
            status_code=422, detail="workspace id must be a uuid"
        )
    return canonical


# ---------------------------------------------------------------------------
# Wire shapes (kept local: this router owns its own contract)
# ---------------------------------------------------------------------------


class CreateWorkspaceRequest(BaseModel):
    """Provision a workspace. The id is supplied by the caller (uc-registry mints
    it; Brand-Ops only mirrors it), never generated here."""

    workspace_id: str
    slug: str


class WorkspaceOut(BaseModel):
    workspace_id: str
    slug: str


class GrantMemberRequest(BaseModel):
    uc_uid: str
    role: str = "member"
    is_default: bool = False


class MemberOut(BaseModel):
    workspace_id: str
    uc_uid: str
    role: str
    is_default: bool
    status: str


class RevokeResult(BaseModel):
    workspace_id: str
    uc_uid: str
    revoked: bool


class DeleteWorkspaceRequest(BaseModel):
    """The offboarding confirmation. ``confirm_workspace_id`` MUST equal the path
    id; the route refuses to delete otherwise, so a tenant teardown cannot fire
    from a stray request."""

    confirm_workspace_id: str


class DeleteWorkspaceResult(BaseModel):
    workspace_id: str
    db_row_deleted: bool
    disk_removed: bool
    brands_removed: list[str] = Field(default_factory=list)
    files_removed: int = 0


class UsageRow(BaseModel):
    workspace_id: str
    slug: str
    brand_count: int
    asset_count: int
    spend_cents: float


class UsageOut(BaseModel):
    generated_at: str
    workspaces: list[UsageRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime provisioning (T2.4)
# ---------------------------------------------------------------------------


@admin_router.post("/workspaces", response_model=WorkspaceOut)
async def create_workspace(body: CreateWorkspaceRequest, admin: _Admin) -> WorkspaceOut:
    """Create (or re-slug) a workspace anchor row at runtime. Idempotent.

    Mirrors the registry-minted id into the tenancy DB via ``upsert_workspace``,
    so a new tenant can be stood up without the old env-allow-list-plus-redeploy
    dance. The id is taken from the body (the caller / uc-registry owns id
    minting); a malformed id is a 422. Calling twice with the same id updates the
    slug and is a no-op otherwise.
    """
    _require_db()
    ws_id = _valid_ws_id(body.workspace_id)
    slug = (body.slug or "").strip()
    if not slug:
        raise HTTPException(status_code=422, detail="slug is required")
    try:
        sm = get_sessionmaker()
        async with sm() as session, session.begin():
            ws = await repo.upsert_workspace(session, workspace_id=ws_id, slug=slug)
    except SQLAlchemyError as exc:
        _log.error("admin create_workspace failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None
    _audit.info("admin %s created/updated workspace %s (slug=%s)", admin.uc_uid, ws_id, slug)
    return WorkspaceOut(workspace_id=str(ws.id), slug=ws.slug)


@admin_router.post("/workspaces/{workspace_id}/members", response_model=MemberOut)
async def grant_member(
    workspace_id: str, body: GrantMemberRequest, admin: _Admin
) -> MemberOut:
    """Grant (or update) a membership in a workspace. Idempotent.

    Upserts one ``workspace_member`` row so the named uc_uid can act in the
    workspace, with the given role and optional default flag. The membership write
    is user-scoped row level security, so it runs under
    ``scoped_session(user_key=...)`` for the granted user (the same scope the boot
    allow-list seeder uses). Re-granting updates the role/default in place.
    """
    _require_db()
    ws_id = _valid_ws_id(workspace_id)
    member_uid = (body.uc_uid or "").strip()
    if not member_uid:
        raise HTTPException(status_code=422, detail="uc_uid is required")
    try:
        sm = get_sessionmaker()
        # User-scoped RLS: the workspace_member policy keys on the member's own
        # user_key, so the write is scoped to the user being granted, not the
        # admin. This is the same path bootstrap.ensure_membership uses.
        async with sm() as session, scoped_session(session, user_key=member_uid):
            member = await repo.upsert_membership(
                session,
                user_key=member_uid,
                workspace_id=ws_id,
                role=(body.role or "member").strip() or "member",
                is_default=body.is_default,
            )
    except SQLAlchemyError as exc:
        _log.error("admin grant_member failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None
    _audit.info(
        "admin %s granted %s role=%s on workspace %s",
        admin.uc_uid,
        member_uid,
        member.role,
        ws_id,
    )
    return MemberOut(
        workspace_id=str(member.workspace_id),
        uc_uid=member.user_key,
        role=member.role,
        is_default=member.is_default,
        status=member.status,
    )


@admin_router.delete("/workspaces/{workspace_id}/members/{uc_uid}", response_model=RevokeResult)
async def revoke_member(workspace_id: str, uc_uid: str, admin: _Admin) -> RevokeResult:
    """Revoke a membership. Idempotent: revoking a non-member is a 200 with
    ``revoked=false``.

    Removes the ``workspace_member`` row for (workspace_id, uc_uid). The
    ``workspace_member`` table is FORCE row-level-secured by the user-scoped
    ``workspace_member_self`` policy (USING user_key = app.current_user_key), so a
    DELETE only matches rows for the user named in the GUC. This runs under
    ``scoped_session(user_key=target)`` -- scoped to the member being revoked, the
    same scope grant uses -- so the policy admits removing exactly that row. (The
    authorization to do this to another user is the admin gate, not the GUC; the
    GUC just has to name the row's owner for FORCE RLS to permit the delete.)
    """
    _require_db()
    ws_id = _valid_ws_id(workspace_id)
    target = (uc_uid or "").strip()
    if not target:
        raise HTTPException(status_code=422, detail="uc_uid is required")
    try:
        sm = get_sessionmaker()
        # Scope to the member being revoked: the user-scoped FORCE-RLS policy on
        # workspace_member filters the DELETE by app.current_user_key, so without
        # this the delete would match zero rows (a silent no-op). Admin-gated.
        async with sm() as session, scoped_session(session, user_key=target):
            revoked = await repo.delete_membership(
                session, workspace_id=ws_id, user_key=target
            )
    except SQLAlchemyError as exc:
        _log.error("admin revoke_member failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None
    _audit.info(
        "admin %s revoked %s on workspace %s (revoked=%s)",
        admin.uc_uid,
        target,
        ws_id,
        revoked,
    )
    return RevokeResult(workspace_id=ws_id, uc_uid=target, revoked=revoked)


# ---------------------------------------------------------------------------
# Offboarding (T2.1): delete the DB row (CASCADE) + purge the on-disk subtree
# ---------------------------------------------------------------------------


@admin_router.delete("/workspaces/{workspace_id}", response_model=DeleteWorkspaceResult)
async def delete_workspace(
    workspace_id: str, body: DeleteWorkspaceRequest, admin: _Admin
) -> DeleteWorkspaceResult:
    """Offboard a tenant: remove all its data, DB and disk. Confirm-gated.

    Two destructive steps, in order:

      1. delete the ``workspace`` anchor row, whose FK CASCADE removes the
         tenant's brands, assets (and their versions), generation jobs, research
         runs, agent governance, spend ledger, and memberships in one statement;
      2. purge the on-disk ``brand-kits/<workspace_id>/`` subtree (a guarded
         rmtree that refuses any path outside the kits root).

    Guarded by ``confirm_workspace_id``: the body field MUST equal the path id, or
    this is a 422 and nothing is touched, so a tenant teardown can never fire by
    accident. Idempotent: a workspace that is already gone (no row, no subtree)
    returns a 200 with both removed flags false rather than a 404, so a retried or
    duplicated offboard is safe. The on-disk purge runs even when the DB row was
    already absent, so a half-finished prior offboard is completed.
    """
    _require_db()
    ws_id = _valid_ws_id(workspace_id)
    if (body.confirm_workspace_id or "").strip() != ws_id:
        raise HTTPException(
            status_code=422,
            detail="confirm_workspace_id must equal the workspace id in the path",
        )

    try:
        sm = get_sessionmaker()
        # DELIBERATE cross-tenant write: the workspace anchor table is NOT
        # row-level-secured (it is the tenant root), so the delete runs without a
        # workspace GUC and the FK CASCADE wipes every scoped child table. Gated
        # by the admin dependency and the confirm field above.
        async with sm() as session, session.begin():
            db_deleted = await repo.delete_workspace(session, ws_id)
    except SQLAlchemyError as exc:
        _log.error("admin delete_workspace (db) failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None

    # Purge the on-disk subtree regardless of whether the row existed, so a prior
    # partial offboard (row gone, files left, or vice versa) is fully cleaned up.
    purge = admin_ops.purge_workspace_tree(ws_id)

    _audit.warning(
        "admin %s DELETED workspace %s (db_row=%s, disk=%s, brands=%s, files=%s)",
        admin.uc_uid,
        ws_id,
        db_deleted,
        purge["removed"],
        purge["brands"],
        purge["file_count"],
    )
    return DeleteWorkspaceResult(
        workspace_id=ws_id,
        db_row_deleted=db_deleted,
        disk_removed=purge["removed"],
        brands_removed=list(purge["brands"]),
        files_removed=int(purge["file_count"]),
    )


# ---------------------------------------------------------------------------
# Data export (T2.1): stream a zip of the tenant's on-disk subtree + manifest
# ---------------------------------------------------------------------------


@admin_router.get("/workspaces/{workspace_id}/export")
async def export_workspace(workspace_id: str, admin: _Admin) -> StreamingResponse:
    """Stream a zip of a workspace's on-disk data plus a manifest.

    The archive holds ``manifest.json`` (workspace id, slug, brand list, brand and
    file counts, an ``exported_at`` stamp) at its top, then every file under
    ``brand-kits/<workspace_id>/`` (brand.yaml, asset sidecars, and the
    generated/ and imported/ trees) under its workspace-relative path. The slug is
    looked up from the DB when available (best effort: a DB hiccup just leaves the
    manifest slug null). A workspace with no files still produces a valid zip with
    just the manifest, so an export never 500s. The temp zip is removed after the
    response is fully streamed.
    """
    ws_id = _valid_ws_id(workspace_id)

    # Best-effort slug lookup for the manifest. The workspace anchor is not
    # RLS-guarded, so no GUC is needed; a DB problem is non-fatal (slug stays
    # null) because the export is fundamentally an on-disk operation.
    slug: str | None = None
    if os.getenv("DATABASE_URL", "").strip():
        try:
            sm = get_sessionmaker()
            async with sm() as session, session.begin():
                ws = await repo.get_workspace(session, ws_id)
                slug = ws.slug if ws is not None else None
        except SQLAlchemyError as exc:
            _log.info("export slug lookup skipped: %s", exc.__class__.__name__)

    try:
        zip_path = admin_ops.build_export_zip(ws_id, slug=slug)
    except OSError as exc:
        _log.error("admin export build failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=500, detail="export build failed") from None

    _audit.info("admin %s exported workspace %s (slug=%s)", admin.uc_uid, ws_id, slug)
    filename = f"brand-ops-{slug or ws_id}.zip"
    # iter_and_cleanup unlinks the temp file in its finally, so the zip is gone
    # once the body is sent (or the client disconnects mid-stream).
    return StreamingResponse(
        admin_ops.iter_and_cleanup(zip_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Cross-tenant usage view (supports T2.5)
# ---------------------------------------------------------------------------


@admin_router.get("/usage", response_model=UsageOut)
async def usage(admin: _Admin) -> UsageOut:
    """Per-workspace brand/asset counts and spend totals, across ALL tenants.

    The one admin view that reads past the per-workspace boundary. It cannot be a
    single unscoped query: the runtime role is NOBYPASSRLS and the scoped tables
    FORCE row level security, so with no workspace GUC every scoped table returns
    zero rows. Instead it enumerates the (non-RLS) workspace anchor table, then
    counts each workspace's data inside a transaction scoped to that one
    workspace, so every count is read under that tenant's own RLS. The result is
    the honest cross-tenant rollup without ever weakening isolation. ``spend_cents``
    is the summed estimated agent spend ledger for the workspace.
    """
    _require_db()
    rows: list[UsageRow] = []
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            # The anchor table is not row-level-secured, so this enumeration of
            # all tenants needs no GUC.
            async with session.begin():
                workspaces = await repo.list_all_workspaces(session)
            for ws in workspaces:
                ws_id = str(ws.workspace_id)
                # Count each tenant UNDER ITS OWN SCOPE: the GUC is set to this
                # workspace, so the FORCE-RLS scoped tables reveal exactly its
                # rows. One scoped transaction per workspace keeps isolation
                # intact instead of bypassing it.
                async with scoped_session(session, workspace_id=ws_id):
                    brand_count, asset_count, spend_cents = await repo.workspace_counts(
                        session, ws_id
                    )
                rows.append(
                    UsageRow(
                        workspace_id=ws_id,
                        slug=ws.slug,
                        brand_count=brand_count,
                        asset_count=asset_count,
                        spend_cents=spend_cents,
                    )
                )
    except SQLAlchemyError as exc:
        _log.error("admin usage view failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None
    _audit.info("admin %s read cross-tenant usage (%d workspaces)", admin.uc_uid, len(rows))
    return UsageOut(generated_at=datetime.now(UTC).isoformat(), workspaces=rows)


__all__ = ("admin_router", "require_admin", "admin_subs")
