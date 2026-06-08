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

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Asset,
    AssetVersion,
    Brand,
    BrandResearchRun,
    GenerationJob,
)

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
