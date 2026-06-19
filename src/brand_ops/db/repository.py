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
from typing import Any

from sqlalchemy import Row, delete, func, select, update
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


async def list_assets(
    session: AsyncSession,
    workspace_id: _UUID,
    brand: str | None = None,
    asset_type: str | None = None,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> Sequence[Asset]:
    stmt = select(Asset).where(Asset.workspace_id == _as_uuid(workspace_id))
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
