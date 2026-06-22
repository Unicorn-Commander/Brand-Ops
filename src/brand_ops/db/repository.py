"""Async CRUD for the catalog, jobs, brands, and research runs.

Every function takes ``workspace_id`` and filters by it in the query itself.
That is belt and suspenders: row level security already constrains the runtime
role to one workspace per transaction (see ``workspace_session``), but passing
the id explicitly keeps the application honest and makes the tenant boundary
visible at every call site. None of these functions commit; the caller owns the
transaction (typically via ``workspace_session``).
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AgentGovernance,
    AgentSpendEvent,
    Asset,
    AssetVersion,
    Brand,
    BrandResearchRun,
    GenerationJob,
    Workspace,
    WorkspaceMember,
)

# Sentinel for "argument not supplied" so a nullable column can be set to NULL
# explicitly (value is None) and distinguished from "leave it unchanged" (value
# is _UNSET). Used by upsert_agent_governance for weekly_budget_cents/model,
# which are both legitimately nullable.
_UNSET: Any = object()

_UUID = uuid.UUID | str


def _as_uuid(value: _UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# --- assets -----------------------------------------------------------------


async def create_asset(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    brand_slug: str,
    asset_type: str,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    mime: str | None = None,
    width: int | None = None,
    height: int | None = None,
    cost_cents: float | None = None,
    storage_key: str | None = None,
    prompt: str | None = None,
    created_by: str | None = None,
) -> Asset:
    asset = Asset(
        workspace_id=_as_uuid(workspace_id),
        brand_slug=brand_slug,
        asset_type=asset_type,
        variant=variant,
        topic=topic,
        platform=platform,
        backend=backend,
        model=model,
        mime=mime,
        width=width,
        height=height,
        cost_cents=cost_cents,
        storage_key=storage_key,
        prompt=prompt,
        created_by=created_by,
    )
    session.add(asset)
    await session.flush()
    return asset


# The asset columns a re-index of the same file may legitimately refresh (a
# compliance re-stamp, a backfill re-run, a regenerated file reusing a path). The
# row's identity (id, workspace_id, storage_key) and its creation facts
# (created_at, created_by) are insert-only and never overwritten.
_ASSET_UPSERT_COLUMNS: tuple[str, ...] = (
    "brand_slug",
    "asset_type",
    "kind",
    "variant",
    "topic",
    "platform",
    "backend",
    "model",
    "mime",
    "width",
    "height",
    "transparent",
    "cost_cents",
    "prompt",
    "reference_images",
    "lifecycle_status",
    "compliance",
    "created_label",
)


async def upsert_asset(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    storage_key: str,
    brand_slug: str,
    asset_type: str,
    kind: str | None = None,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
    model: str | None = None,
    mime: str | None = None,
    width: int | None = None,
    height: int | None = None,
    transparent: bool | None = None,
    cost_cents: float | None = None,
    prompt: str | None = None,
    reference_images: list | None = None,
    lifecycle_status: str | None = None,
    compliance: dict | None = None,
    created_label: str | None = None,
    created_by: str | None = None,
) -> None:
    """Insert or refresh the catalog row for one stored file, keyed on its path.

    The natural key is ``(workspace_id, storage_key)`` -- one row per file in a
    workspace -- so this is the idempotent write behind both the dual-write (a new
    generation/upload) and the one-time backfill (re-runnable). On a conflict it
    refreshes the descriptive + lifecycle columns (so a compliance re-stamp or a
    backfill re-run reflects the latest sidecar) but never the row's identity or
    creation facts. Runs under the caller's workspace scope: the INSERT and the
    UPDATE branch both satisfy the asset RLS ``WITH CHECK`` because
    ``workspace_id`` is the scoped workspace, and the conflict can only match a row
    in that same workspace (the unique index leads with ``workspace_id`` and RLS
    hides the rest). ``storage_key`` is required -- a keyless row could not upsert.
    Does not commit; the caller owns the transaction.
    """
    values = {
        "workspace_id": _as_uuid(workspace_id),
        "storage_key": storage_key,
        "brand_slug": brand_slug,
        "asset_type": asset_type,
        "kind": kind,
        "variant": variant,
        "topic": topic,
        "platform": platform,
        "backend": backend,
        "model": model,
        "mime": mime,
        "width": width,
        "height": height,
        "transparent": transparent,
        "cost_cents": cost_cents,
        "prompt": prompt,
        "reference_images": reference_images,
        "lifecycle_status": lifecycle_status,
        "compliance": compliance,
        "created_label": created_label,
        "created_by": created_by,
    }
    stmt = pg_insert(Asset).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["workspace_id", "storage_key"],
        index_where=text("storage_key IS NOT NULL"),
        set_={col: getattr(stmt.excluded, col) for col in _ASSET_UPSERT_COLUMNS},
    )
    await session.execute(stmt)
    await session.flush()


async def list_assets(
    session: AsyncSession,
    workspace_id: _UUID,
    brand: str | None = None,
    asset_type: str | None = None,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> Sequence[Asset]:
    # Soft-deleted assets never appear in the catalog: a recoverable delete stamps
    # deleted_at, and this base filter hides every such row from every list read
    # until the retention sweeper hard-purges it (see soft_delete_asset / migration
    # 0007). A live asset has deleted_at IS NULL.
    stmt = select(Asset).where(
        Asset.workspace_id == _as_uuid(workspace_id),
        Asset.deleted_at.is_(None),
    )
    if brand is not None:
        stmt = stmt.where(Asset.brand_slug == brand)
    if asset_type is not None:
        stmt = stmt.where(Asset.asset_type == asset_type)
    stmt = stmt.order_by(Asset.created_at.desc())
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_asset(
    session: AsyncSession, workspace_id: _UUID, asset_id: _UUID
) -> Asset | None:
    stmt = select(Asset).where(
        Asset.workspace_id == _as_uuid(workspace_id),
        Asset.id == _as_uuid(asset_id),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def add_asset_version(
    session: AsyncSession,
    workspace_id: _UUID,
    asset_id: _UUID,
    *,
    storage_key: str | None = None,
    version_no: int | None = None,
) -> AssetVersion | None:
    """Append a version to an asset, auto-numbering when version_no is omitted.

    Returns None when the asset is not in this workspace, so a caller cannot
    add a version to another tenant's asset.
    """
    asset = await get_asset(session, workspace_id, asset_id)
    if asset is None:
        return None
    if version_no is None:
        current = await session.execute(
            select(func.coalesce(func.max(AssetVersion.version_no), 0)).where(
                AssetVersion.asset_id == asset.id
            )
        )
        version_no = int(current.scalar_one()) + 1
    version = AssetVersion(
        asset_id=asset.id,
        version_no=version_no,
        storage_key=storage_key,
    )
    session.add(version)
    await session.flush()
    return version


# --- soft-delete + retention ------------------------------------------------
# The recoverable delete (soft_delete_asset) and the retention sweep
# (list_soft_deleted_before -> purge_asset). A soft-delete stamps deleted_at so
# list_assets stops returning the row; the sweep finds rows whose tombstone has
# aged past the retention window and hard-deletes them. All three are
# workspace-scoped (filter on workspace_id over RLS, belt-and-suspenders) and do
# not commit; the caller owns the transaction.


async def soft_delete_asset(
    session: AsyncSession, workspace_id: _UUID, *, storage_key: str
) -> bool:
    """Hide the catalog row for one stored file by stamping ``deleted_at`` now.

    Sets ``deleted_at = now()`` on the live (not-already-deleted) ``asset`` row
    keyed by ``(workspace_id, storage_key)``, so every catalog list read stops
    returning it (``list_assets`` filters ``deleted_at IS NULL``). The row and the
    file survive until the retention sweeper purges them, so the delete is
    recoverable. The ``deleted_at IS NULL`` predicate makes a repeat delete a
    no-op: returns True only when THIS call tombstoned a live row (rowcount == 1),
    False when there was no matching live row (already deleted, or no such key).
    Workspace-scoped (the update also satisfies the asset RLS WITH CHECK because
    workspace_id is the scoped tenant); does not commit.
    """
    result = await session.execute(
        update(Asset)
        .where(
            Asset.workspace_id == _as_uuid(workspace_id),
            Asset.storage_key == storage_key,
            Asset.deleted_at.is_(None),
        )
        .values(deleted_at=func.now())
    )
    return result.rowcount == 1


async def list_soft_deleted_before(
    session: AsyncSession, workspace_id: _UUID, cutoff: Any
) -> Sequence[Asset]:
    """The workspace's soft-deleted assets whose tombstone predates ``cutoff``.

    The retention sweep candidates: rows with a non-null ``deleted_at`` strictly
    older than ``cutoff`` (now minus the retention window). A just-deleted row
    inside the window is excluded, so it stays recoverable until it ages out.
    ``cutoff`` is a timezone-aware datetime. Filters by ``workspace_id`` explicitly
    over RLS; the caller owns the transaction.
    """
    stmt = (
        select(Asset)
        .where(
            Asset.workspace_id == _as_uuid(workspace_id),
            Asset.deleted_at.is_not(None),
            Asset.deleted_at < cutoff,
        )
        .order_by(Asset.deleted_at)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def purge_asset(
    session: AsyncSession, workspace_id: _UUID, asset_id: _UUID
) -> None:
    """Hard-delete one asset row by id, scoped to its workspace (the sweep's write).

    The irreversible counterpart to ``soft_delete_asset``: removes the catalog
    row entirely (its ``asset_version`` rows cascade) once the sweeper has aged it
    out and deleted its on-disk bytes. Filters by ``workspace_id`` + ``id`` so it
    can only purge a row in the scoped tenant; does not commit.
    """
    await session.execute(
        delete(Asset).where(
            Asset.workspace_id == _as_uuid(workspace_id),
            Asset.id == _as_uuid(asset_id),
        )
    )


# --- generation jobs --------------------------------------------------------


async def create_job(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    kind: str,
    status: str = "pending",
    request: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> GenerationJob:
    job = GenerationJob(
        workspace_id=_as_uuid(workspace_id),
        kind=kind,
        status=status,
        request=request,
        created_by=created_by,
    )
    session.add(job)
    await session.flush()
    return job


async def update_job(
    session: AsyncSession,
    workspace_id: _UUID,
    job_id: _UUID,
    *,
    status: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> GenerationJob | None:
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status
    if result is not None:
        values["result"] = result
    if error is not None:
        values["error"] = error
    if values:
        await session.execute(
            update(GenerationJob)
            .where(
                GenerationJob.workspace_id == _as_uuid(workspace_id),
                GenerationJob.id == _as_uuid(job_id),
            )
            .values(**values)
        )
        await session.flush()
    return await get_job(session, workspace_id, job_id)


async def get_job(
    session: AsyncSession, workspace_id: _UUID, job_id: _UUID
) -> GenerationJob | None:
    stmt = select(GenerationJob).where(
        GenerationJob.workspace_id == _as_uuid(workspace_id),
        GenerationJob.id == _as_uuid(job_id),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    kind: str | None = None,
    status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> Sequence[GenerationJob]:
    stmt = select(GenerationJob).where(
        GenerationJob.workspace_id == _as_uuid(workspace_id)
    )
    if kind is not None:
        stmt = stmt.where(GenerationJob.kind == kind)
    if status is not None:
        stmt = stmt.where(GenerationJob.status == status)
    stmt = stmt.order_by(GenerationJob.created_at.desc())
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


# --- brands -----------------------------------------------------------------


async def upsert_brand(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    slug: str,
    display_name: str,
    parent_slug: str | None = None,
    tagline: str | None = None,
    status: str = "active",
    created_by: str | None = None,
) -> Brand:
    """Insert a brand, or update the existing one with the same workspace+slug."""
    ws_id = _as_uuid(workspace_id)
    existing = await session.execute(
        select(Brand).where(Brand.workspace_id == ws_id, Brand.slug == slug)
    )
    brand = existing.scalar_one_or_none()
    if brand is None:
        brand = Brand(
            workspace_id=ws_id,
            slug=slug,
            display_name=display_name,
            parent_slug=parent_slug,
            tagline=tagline,
            status=status,
            created_by=created_by,
        )
        session.add(brand)
    else:
        brand.display_name = display_name
        brand.parent_slug = parent_slug
        brand.tagline = tagline
        brand.status = status
    await session.flush()
    return brand


async def list_brands(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    status: str | None = None,
) -> Sequence[Brand]:
    stmt = select(Brand).where(Brand.workspace_id == _as_uuid(workspace_id))
    if status is not None:
        stmt = stmt.where(Brand.status == status)
    stmt = stmt.order_by(Brand.slug)
    result = await session.execute(stmt)
    return result.scalars().all()


# --- brand research runs ----------------------------------------------------


async def create_research_run(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    target_company: str | None = None,
    target_domain: str | None = None,
    status: str = "pending",
    profile: dict[str, Any] | None = None,
    sources: dict[str, Any] | None = None,
) -> BrandResearchRun:
    run = BrandResearchRun(
        workspace_id=_as_uuid(workspace_id),
        target_company=target_company,
        target_domain=target_domain,
        status=status,
        profile=profile,
        sources=sources,
    )
    session.add(run)
    await session.flush()
    return run


async def update_research_run(
    session: AsyncSession,
    workspace_id: _UUID,
    run_id: _UUID,
    *,
    status: str | None = None,
    profile: dict[str, Any] | None = None,
    sources: dict[str, Any] | None = None,
) -> BrandResearchRun | None:
    values: dict[str, Any] = {}
    if status is not None:
        values["status"] = status
    if profile is not None:
        values["profile"] = profile
    if sources is not None:
        values["sources"] = sources
    if values:
        await session.execute(
            update(BrandResearchRun)
            .where(
                BrandResearchRun.workspace_id == _as_uuid(workspace_id),
                BrandResearchRun.id == _as_uuid(run_id),
            )
            .values(**values)
        )
        await session.flush()
    stmt = select(BrandResearchRun).where(
        BrandResearchRun.workspace_id == _as_uuid(workspace_id),
        BrandResearchRun.id == _as_uuid(run_id),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# --- workspaces + memberships -----------------------------------------------
# These back the tenancy resolver and the seed/mirror path. The ``workspace``
# table is the tenant anchor (not row-level-secured); ``workspace_member`` is
# user-scoped RLS, so a membership read/write needs app.current_user_key set to
# the user (see db.base.scoped_session).


async def upsert_workspace(
    session: AsyncSession, *, workspace_id: _UUID, slug: str
) -> Workspace:
    """Insert the workspace row, or update its slug. A mirror of the registry.

    Brand-Ops never mints a workspace id (uc-registry is the sole minter); it
    receives the canonical id and records it here so brands/assets can foreign
    key to it. Keyed on the supplied id, so calling twice is idempotent.
    """
    ws_id = _as_uuid(workspace_id)
    existing = await session.get(Workspace, ws_id)
    if existing is None:
        ws = Workspace(id=ws_id, slug=slug)
        session.add(ws)
    else:
        existing.slug = slug
        ws = existing
    await session.flush()
    return ws


async def workspace_slug_exists(session: AsyncSession, slug: str) -> bool:
    """True when any workspace already uses ``slug``. Needs no workspace GUC.

    The ``workspace`` table is the tenant anchor and is NOT row-level-secured, so
    this global existence check (used by self-serve signup to pick a free slug)
    runs without a scope. The slug column is unique, so a hit means exactly one
    row; the unique constraint remains the final backstop against a concurrent
    check-then-insert race.
    """
    stmt = select(Workspace.id).where(Workspace.slug == slug).limit(1)
    return (await session.execute(stmt)).first() is not None


async def list_user_memberships(
    session: AsyncSession, user_key: str
) -> Sequence[Row[Any]]:
    """Active memberships for a user (default first), joined to the workspace slug.

    Reads the user-scoped workspace_member policy, so app.current_user_key must
    equal ``user_key`` on the transaction; the explicit user_key predicate is
    belt-and-suspenders. Returns rows of (workspace_id, slug, role, is_default,
    status) -- the raw material for the switcher and the default resolution.
    """
    stmt = (
        select(
            WorkspaceMember.workspace_id,
            Workspace.slug,
            WorkspaceMember.role,
            WorkspaceMember.is_default,
            WorkspaceMember.status,
        )
        .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
        .where(
            WorkspaceMember.user_key == user_key,
            WorkspaceMember.status == "active",
        )
        .order_by(WorkspaceMember.is_default.desc(), Workspace.slug)
    )
    result = await session.execute(stmt)
    return result.all()


async def is_active_member(
    session: AsyncSession, user_key: str, workspace_id: _UUID
) -> bool:
    """True when ``user_key`` has an active membership in ``workspace_id``."""
    stmt = select(WorkspaceMember.role).where(
        WorkspaceMember.user_key == user_key,
        WorkspaceMember.workspace_id == _as_uuid(workspace_id),
        WorkspaceMember.status == "active",
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def upsert_membership(
    session: AsyncSession,
    *,
    user_key: str,
    workspace_id: _UUID,
    role: str = "member",
    status: str = "active",
    is_default: bool = False,
) -> WorkspaceMember:
    """Insert or update one membership row.

    The user-scoped RLS policy requires app.current_user_key to equal
    ``user_key`` for both the read and the write check, so seed/mirror a row only
    inside ``scoped_session(session, user_key=user_key)``.
    """
    ws_id = _as_uuid(workspace_id)
    existing = await session.get(
        WorkspaceMember, {"user_key": user_key, "workspace_id": ws_id}
    )
    if existing is None:
        member = WorkspaceMember(
            user_key=user_key,
            workspace_id=ws_id,
            role=role,
            status=status,
            is_default=is_default,
        )
        session.add(member)
    else:
        existing.role = role
        existing.status = status
        existing.is_default = is_default
        member = existing
    await session.flush()
    return member


async def list_brand_slugs(
    session: AsyncSession, workspace_id: _UUID
) -> frozenset[str]:
    """The set of brand slugs a workspace owns -- the read-scope allow-list.

    The tenancy resolver loads this once per request and hands it to the file
    brand loader, which then only ever reveals brands in the set. Empty for a
    workspace with no brands (or for a caller with no active workspace).
    """
    stmt = select(Brand.slug).where(Brand.workspace_id == _as_uuid(workspace_id))
    result = await session.execute(stmt)
    return frozenset(result.scalars().all())


# --- agent governance + spend -----------------------------------------------
# Both tables are workspace-scoped RLS (migration 0003). As everywhere in this
# module, each query also filters by workspace_id explicitly (belt-and-suspenders
# over RLS) and none of these commit; the caller owns the transaction via
# ``workspace_session``.


async def get_agent_governance(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    agent_kind: str,
    agent_ref: str,
) -> AgentGovernance | None:
    """The saved governance row for one agent, or None when it has none yet."""
    stmt = select(AgentGovernance).where(
        AgentGovernance.workspace_id == _as_uuid(workspace_id),
        AgentGovernance.agent_kind == agent_kind,
        AgentGovernance.agent_ref == agent_ref,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_agent_governance(
    session: AsyncSession, workspace_id: _UUID
) -> Sequence[AgentGovernance]:
    """Every saved governance row in the workspace (a stable order)."""
    stmt = (
        select(AgentGovernance)
        .where(AgentGovernance.workspace_id == _as_uuid(workspace_id))
        .order_by(AgentGovernance.agent_kind, AgentGovernance.agent_ref)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def upsert_agent_governance(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    agent_kind: str,
    agent_ref: str,
    autonomy: str | None = None,
    standing_orders: str | None = _UNSET,
    weekly_budget_cents: int | None = _UNSET,
    model: str | None = _UNSET,
    updated_by: str | None = None,
) -> AgentGovernance:
    """Insert or update one agent's governance row, keyed by (ws, kind, ref).

    Only supplied fields are applied. ``weekly_budget_cents`` and ``model`` use
    the ``_UNSET`` sentinel so passing ``None`` clears the column (no cap / the
    agent default) while omitting the argument leaves the stored value untouched
    -- the two are different intents on a nullable column. ``autonomy`` and
    ``standing_orders`` use ``None``/``_UNSET`` the same way; on first insert any
    omitted field falls to the column default (autonomy -> 'propose'). Flushes so
    the caller can read server-side defaults back, but does not commit.
    """
    ws_id = _as_uuid(workspace_id)
    existing = await session.execute(
        select(AgentGovernance).where(
            AgentGovernance.workspace_id == ws_id,
            AgentGovernance.agent_kind == agent_kind,
            AgentGovernance.agent_ref == agent_ref,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = AgentGovernance(
            workspace_id=ws_id,
            agent_kind=agent_kind,
            agent_ref=agent_ref,
        )
        if autonomy is not None:
            row.autonomy = autonomy
        if standing_orders is not _UNSET:
            row.standing_orders = standing_orders
        if weekly_budget_cents is not _UNSET:
            row.weekly_budget_cents = weekly_budget_cents
        if model is not _UNSET:
            row.model = model
        row.updated_by = updated_by
        session.add(row)
    else:
        if autonomy is not None:
            row.autonomy = autonomy
        if standing_orders is not _UNSET:
            row.standing_orders = standing_orders
        if weekly_budget_cents is not _UNSET:
            row.weekly_budget_cents = weekly_budget_cents
        if model is not _UNSET:
            row.model = model
        if updated_by is not None:
            row.updated_by = updated_by
    await session.flush()
    return row


async def record_spend_event(
    session: AsyncSession,
    workspace_id: _UUID,
    *,
    agent_kind: str,
    agent_ref: str,
    kind: str,
    brand_slug: str | None = None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    cost_cents: float | None = None,
) -> AgentSpendEvent:
    """Append one spend event to the ledger. Caller owns the transaction."""
    event = AgentSpendEvent(
        workspace_id=_as_uuid(workspace_id),
        agent_kind=agent_kind,
        agent_ref=agent_ref,
        kind=kind,
        brand_slug=brand_slug,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=cost_cents,
    )
    session.add(event)
    await session.flush()
    return event


async def workspace_image_spend_since(
    session: AsyncSession, workspace_id: _UUID, since: Any
) -> float:
    """Total image spend (cents) for one workspace at or after ``since``.

    Sums ``cost_cents`` over the workspace's ``kind='image'`` events in the
    window, coalescing to 0.0 so a workspace with no image spend reports 0.0 not
    NULL. This is the per-tenant image cap's accumulator: the engine compares it
    against ``BRAND_OPS_IMAGE_CAP_CENTS`` before generating, so one tenant can no
    longer drain the shared image budget for the rest. Filters by ``workspace_id``
    explicitly over RLS (belt-and-suspenders); the caller owns the transaction.
    """
    stmt = select(func.coalesce(func.sum(AgentSpendEvent.cost_cents), 0)).where(
        AgentSpendEvent.workspace_id == _as_uuid(workspace_id),
        AgentSpendEvent.kind == "image",
        AgentSpendEvent.created_at >= since,
    )
    return float((await session.execute(stmt)).scalar_one())


async def agent_spend_total_since(
    session: AsyncSession,
    workspace_id: _UUID,
    since: Any,
    *,
    agent_kind: str,
    agent_ref: str,
) -> float:
    """Total spend (cents) for ONE agent in this workspace at or after ``since``.

    Sums ``cost_cents`` across every kind (chat and image) the agent accrued in
    the window, coalescing to 0.0. This is the accumulator the ``weekly_budget_cents``
    gate compares against before an agent-attributed charge: when the agent has a
    budget and the window total already meets it, the engine denies the next
    generation (``QuotaExceeded``). Filters by ``workspace_id`` + the agent key
    explicitly over RLS; the caller owns the transaction.
    """
    stmt = select(func.coalesce(func.sum(AgentSpendEvent.cost_cents), 0)).where(
        AgentSpendEvent.workspace_id == _as_uuid(workspace_id),
        AgentSpendEvent.agent_kind == agent_kind,
        AgentSpendEvent.agent_ref == agent_ref,
        AgentSpendEvent.created_at >= since,
    )
    return float((await session.execute(stmt)).scalar_one())


async def agent_spend_since(
    session: AsyncSession, workspace_id: _UUID, since: Any
) -> Sequence[Row[Any]]:
    """Per-agent spend totals since ``since``, grouped by (agent_kind, agent_ref).

    Returns rows of (agent_kind, agent_ref, total_tokens, cost_cents, calls)
    where total_tokens and cost_cents are SUMs (NULL when a group has no numeric
    values) and calls is the row count. ``since`` is a timezone-aware datetime;
    only events at or after it are counted. The API joins these onto the agent
    list to show real 7-day spend per agent.
    """
    stmt = (
        select(
            AgentSpendEvent.agent_kind,
            AgentSpendEvent.agent_ref,
            func.coalesce(func.sum(AgentSpendEvent.total_tokens), 0).label(
                "total_tokens"
            ),
            func.coalesce(func.sum(AgentSpendEvent.cost_cents), 0).label("cost_cents"),
            func.count().label("calls"),
        )
        .where(
            AgentSpendEvent.workspace_id == _as_uuid(workspace_id),
            AgentSpendEvent.created_at >= since,
        )
        .group_by(AgentSpendEvent.agent_kind, AgentSpendEvent.agent_ref)
    )
    result = await session.execute(stmt)
    return result.all()


# --- admin: offboarding + provisioning + the cross-tenant view --------------
# These back the admin API (api/admin.py). They are the deliberate exception to
# this module's per-workspace rule: a tenant DELETE and the cross-tenant usage
# read necessarily act outside one workspace, so they DO NOT take the workspace
# GUC the way every function above does. They are admin-gated at the route, and
# each is commented where it crosses the tenant boundary so the bypass is never
# accidental. None commit; the caller owns the transaction.


async def get_workspace(
    session: AsyncSession, workspace_id: _UUID
) -> Workspace | None:
    """The workspace anchor row, or None when no such workspace exists.

    The ``workspace`` table is the tenant root and is NOT row-level-secured, so
    this read needs no GUC. Admin-only via the route; used to 404 a delete or an
    export of a workspace that does not exist before any destructive work.
    """
    return await session.get(Workspace, _as_uuid(workspace_id))


async def delete_workspace(session: AsyncSession, workspace_id: _UUID) -> bool:
    """Delete the workspace anchor row; the FK CASCADE removes all its data.

    Every workspace-scoped table (brand, asset, generation_job,
    brand_research_run, agent_governance, agent_spend_event, workspace_member)
    declares ``ForeignKey(workspace.id, ondelete='CASCADE')``, and asset_version
    cascades from asset, so deleting this one row removes the tenant's entire DB
    footprint in a single statement. DELIBERATE cross-tenant write: the
    ``workspace`` table is not RLS-guarded (it is the tenant root), so this runs
    without a workspace GUC; it is admin-gated at the route. Returns True when a
    row was deleted, False when the workspace did not exist (idempotent: a second
    delete is a no-op False). The caller owns the transaction / commit.
    """
    result = await session.execute(
        delete(Workspace).where(Workspace.id == _as_uuid(workspace_id))
    )
    return bool(result.rowcount)


async def delete_membership(
    session: AsyncSession, *, workspace_id: _UUID, user_key: str
) -> bool:
    """Revoke one membership row (admin provisioning). Idempotent.

    Deletes by the (workspace_id, user_key) primary key. The ``workspace_member``
    table is FORCE row-level-secured by the user-scoped ``workspace_member_self``
    policy, which filters even a DELETE by ``app.current_user_key``; so the caller
    MUST run this inside ``scoped_session(user_key=user_key)`` (scoped to the
    member being revoked) or the delete matches zero rows. The admin revoke route
    does exactly that -- the admin gate is the authorization to act on someone
    else's row, the GUC just names the row's owner so FORCE RLS permits it.
    Returns True when a row was removed, False when there was none (so re-revoking
    is a safe no-op).
    """
    result = await session.execute(
        delete(WorkspaceMember).where(
            WorkspaceMember.workspace_id == _as_uuid(workspace_id),
            WorkspaceMember.user_key == user_key,
        )
    )
    return bool(result.rowcount)


async def list_all_workspaces(session: AsyncSession) -> Sequence[Row[Any]]:
    """Every workspace anchor row (id, slug), slug-ordered. Admin-only.

    The ``workspace`` table is the tenant root and is NOT row-level-secured, so
    this enumerates all tenants WITHOUT a workspace GUC. It is the seed of the
    cross-tenant usage view: the admin route walks these rows and, for each, opens
    a transaction scoped to that one workspace to count its data under RLS (rather
    than trying to read every tenant's scoped tables at once, which the
    NOBYPASSRLS runtime role with FORCE row level security correctly forbids).
    """
    stmt = select(Workspace.id.label("workspace_id"), Workspace.slug.label("slug")).order_by(
        Workspace.slug
    )
    result = await session.execute(stmt)
    return result.all()


async def workspace_counts(
    session: AsyncSession, workspace_id: _UUID
) -> tuple[int, int, float]:
    """(brand_count, asset_count, spend_cents) for ONE workspace, under its scope.

    Counts the brand and asset rows and sums the spend ledger for a single
    workspace. Like every per-workspace read in this module it filters by
    ``workspace_id`` explicitly and relies on the row level security the caller
    has set: the admin route calls this inside ``scoped_session(workspace_id=...)``
    for each workspace in turn, so each count sees exactly that tenant's rows.
    Two cheap COUNTs plus one SUM; ``cost_cents`` is coalesced to 0 so a workspace
    with no spend reports 0.0, not NULL.
    """
    ws_id = _as_uuid(workspace_id)
    brand_count = (
        await session.execute(
            select(func.count()).select_from(Brand).where(Brand.workspace_id == ws_id)
        )
    ).scalar_one()
    asset_count = (
        await session.execute(
            select(func.count()).select_from(Asset).where(Asset.workspace_id == ws_id)
        )
    ).scalar_one()
    spend_cents = (
        await session.execute(
            select(func.coalesce(func.sum(AgentSpendEvent.cost_cents), 0)).where(
                AgentSpendEvent.workspace_id == ws_id
            )
        )
    ).scalar_one()
    return int(brand_count), int(asset_count), float(spend_cents)


# --- admin: read-only DB export for the portability archive ------------------
# The export route dumps a tenant's DB-only rows (the data delete_workspace's
# CASCADE would erase) so an export is the inverse of an offboard. These helpers
# are READ-ONLY and run UNDER THE WORKSPACE'S OWN SCOPE: the route wraps each in
# ``scoped_session(workspace_id=...)``, so every workspace-scoped table is filtered
# to the tenant by row level security, and each query also filters by
# ``workspace_id`` explicitly (belt-and-suspenders), the same posture as every
# read above. asset_version is the one table with no workspace_id column and no
# RLS policy of its own (it cascades from asset), so it is constrained to the
# tenant by an explicit join onto the workspace's assets rather than read raw.


def _row_to_jsonable(obj: Any) -> dict[str, Any]:
    """A JSON-safe dict for one ORM row: uuids, datetimes, and Decimals to str.

    Walks the mapped columns (so it does not pull in relationships or unloaded
    attributes) and coerces the types the JSON encoder cannot take natively:
    uuid and datetime to their ISO/canonical string, Decimal to float (cost
    columns are Numeric). Everything else passes through. Used only by the export
    helpers below to render rows into the archive's db/ JSON.
    """
    out: dict[str, Any] = {}
    for column in obj.__table__.columns:
        value = getattr(obj, column.name)
        if isinstance(value, uuid.UUID):
            out[column.name] = str(value)
        elif isinstance(value, datetime):
            out[column.name] = value.isoformat()
        elif isinstance(value, Decimal):
            out[column.name] = float(value)
        else:
            out[column.name] = value
    return out


async def export_workspace_rows(
    session: AsyncSession, workspace_id: _UUID
) -> dict[str, list[dict[str, Any]]]:
    """Every DB row a workspace owns, by table, as JSON-safe dicts. Read-only.

    Returns ``{table_name: [row, ...]}`` for exactly the tables
    ``delete_workspace`` would CASCADE-erase: brand, asset, asset_version,
    generation_job, brand_research_run, agent_governance, agent_spend_event, and
    workspace_member. The route calls this inside
    ``scoped_session(workspace_id=...)`` so the workspace-scoped tables are
    revealed only for this tenant under row level security; asset_version (no
    workspace_id, not RLS-guarded) is scoped by joining onto this workspace's
    asset ids, so it can never leak another tenant's versions. Stable ordering
    (created_at / primary key) so two exports of an unchanged workspace produce
    the same archive. workspace_member is keyed on workspace_id and is included so
    the membership graph is part of the portability dump.
    """
    ws_id = _as_uuid(workspace_id)

    brands = (
        (
            await session.execute(
                select(Brand)
                .where(Brand.workspace_id == ws_id)
                .order_by(Brand.slug)
            )
        )
        .scalars()
        .all()
    )
    assets = (
        (
            await session.execute(
                select(Asset)
                .where(Asset.workspace_id == ws_id)
                .order_by(Asset.created_at, Asset.id)
            )
        )
        .scalars()
        .all()
    )
    # asset_version has no workspace_id and no RLS policy of its own, so it is
    # scoped to this tenant via an explicit join onto the workspace's assets
    # (Asset itself is workspace-filtered and RLS-guarded). Without the join a
    # raw select would return every tenant's versions.
    versions = (
        (
            await session.execute(
                select(AssetVersion)
                .join(Asset, Asset.id == AssetVersion.asset_id)
                .where(Asset.workspace_id == ws_id)
                .order_by(AssetVersion.asset_id, AssetVersion.version_no)
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        (
            await session.execute(
                select(GenerationJob)
                .where(GenerationJob.workspace_id == ws_id)
                .order_by(GenerationJob.created_at, GenerationJob.id)
            )
        )
        .scalars()
        .all()
    )
    runs = (
        (
            await session.execute(
                select(BrandResearchRun)
                .where(BrandResearchRun.workspace_id == ws_id)
                .order_by(BrandResearchRun.created_at, BrandResearchRun.id)
            )
        )
        .scalars()
        .all()
    )
    governance = (
        (
            await session.execute(
                select(AgentGovernance)
                .where(AgentGovernance.workspace_id == ws_id)
                .order_by(AgentGovernance.agent_kind, AgentGovernance.agent_ref)
            )
        )
        .scalars()
        .all()
    )
    spend = (
        (
            await session.execute(
                select(AgentSpendEvent)
                .where(AgentSpendEvent.workspace_id == ws_id)
                .order_by(AgentSpendEvent.created_at, AgentSpendEvent.id)
            )
        )
        .scalars()
        .all()
    )
    # workspace_member is USER-scoped under RLS (0002), so a workspace-only scope
    # reads none of it. Opt into the SELECT-only export policy (0005) for THIS
    # transaction by setting the membership-export GUC LOCAL; with the workspace
    # already scoped, the policy then reveals exactly this tenant's members. The
    # flag is local, so it reverts at commit and never leaks to the next request,
    # and a normal request (which never sets it) still cannot enumerate co-members.
    await session.execute(
        text("SELECT set_config('app.membership_export', 'on', true)")
    )
    members = (
        (
            await session.execute(
                select(WorkspaceMember)
                .where(WorkspaceMember.workspace_id == ws_id)
                .order_by(WorkspaceMember.user_key)
            )
        )
        .scalars()
        .all()
    )

    return {
        "brand": [_row_to_jsonable(r) for r in brands],
        "asset": [_row_to_jsonable(r) for r in assets],
        "asset_version": [_row_to_jsonable(r) for r in versions],
        "generation_job": [_row_to_jsonable(r) for r in jobs],
        "brand_research_run": [_row_to_jsonable(r) for r in runs],
        "agent_governance": [_row_to_jsonable(r) for r in governance],
        "agent_spend_event": [_row_to_jsonable(r) for r in spend],
        "workspace_member": [_row_to_jsonable(r) for r in members],
    }
