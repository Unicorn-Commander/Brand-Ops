"""Idempotent boot-time tenancy seed + the file->DB brand importer.

Run on container boot (best-effort, after migrations), this makes the canonical
DB reflect reality without losing anything:

  * ensure the deploy's bootstrap workspace row exists -- the single tenant whose
    file brands these are (Magic Unicorn on the dogfood). Brand-Ops mirrors the
    registry-minted id; it never mints one.
  * index every file brand into the DB, stamped with its workspace (the brand's
    own ``workspace_id`` when it carries one, else the bootstrap workspace), so
    the read path's allow-list reflects the brands actually on the volume.
  * apply the membership allow-list (SUITE-IDENTITY D5: allow-lists seed
    memberships, memberships gate) so the seeded operators can see their brands.

Brand-only by design: tenant isolation is enforced at the brand boundary (a
brand belongs to one workspace; its file assets inherit that scope through the
read gate), so the file asset catalog is not duplicated into the DB here.

Every step is idempotent and guarded: a database that is unset or unreachable is
logged and skipped, never crash-looping the app -- a downed dependency must not
take the surface down with it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from . import repository as repo
from .base import get_sessionmaker, scoped_session
from .tenancy import coerce_uuid

_log = logging.getLogger("brand_ops.db.bootstrap")


def _bootstrap_workspace() -> tuple[str | None, str]:
    """The (workspace_id, slug) this deploy's file brands belong to, from env."""
    ws_id = coerce_uuid(os.getenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "").strip() or None)
    slug = os.getenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_SLUG", "").strip() or "magic-unicorn"
    return ws_id, slug


def parse_allowlist(raw: str) -> list[dict]:
    """Parse ``BRAND_OPS_MEMBER_ALLOWLIST`` = ``<sub>:<ws_id>:<role>[:default], ...``.

    ``user_key`` (a uuid sub) and ``workspace_id`` are uuids with no colons, so
    ':' is an unambiguous field separator. A malformed entry is skipped (logged),
    never fatal.
    """
    entries: list[dict] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]
        ws = coerce_uuid(parts[1]) if len(parts) > 1 else None
        if len(parts) < 2 or not parts[0] or ws is None:
            _log.warning("ignoring malformed allowlist entry: %r", chunk)
            continue
        entries.append(
            {
                "user_key": parts[0],
                "workspace_id": ws,
                "role": parts[2] if len(parts) > 2 and parts[2] else "member",
                "is_default": len(parts) > 3 and parts[3].lower() in ("default", "1", "true"),
            }
        )
    return entries


async def ensure_workspace(workspace_id: str, slug: str) -> None:
    """Idempotently ensure the workspace anchor row exists (registry mirror)."""
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            await repo.upsert_workspace(session, workspace_id=workspace_id, slug=slug)


async def ensure_membership(
    *,
    user_key: str,
    workspace_id: str,
    role: str = "member",
    is_default: bool = False,
) -> None:
    """Idempotently ensure one membership row exists (user-scoped write)."""
    sm = get_sessionmaker()
    async with sm() as session:
        async with scoped_session(session, user_key=user_key):
            await repo.upsert_membership(
                session,
                user_key=user_key,
                workspace_id=workspace_id,
                role=role,
                is_default=is_default,
            )


async def import_file_brands(
    bootstrap_workspace_id: str, kits_dir: Path | None = None
) -> dict[str, int]:
    """Index every file brand into the DB, stamped with its workspace.

    A brand carrying its own ``workspace_id`` (created by the scoped write path)
    keeps it; a brand without one (the seed brands) is assigned to
    ``bootstrap_workspace_id``. Idempotent: ``upsert_brand`` is keyed on
    (workspace_id, slug), so re-running only updates. Returns per-workspace
    counts for logging.
    """
    from ..brand_loader import BrandNotFound, list_brands, load_brand

    sm = get_sessionmaker()
    counts: dict[str, int] = {}
    async with sm() as session:
        for slug in list_brands(kits_dir=kits_dir):
            try:
                brand = load_brand(slug, kits_dir=kits_dir)
            except (BrandNotFound, ValueError):
                continue
            meta = brand.brand
            ws = coerce_uuid(getattr(meta, "workspace_id", None)) or bootstrap_workspace_id
            async with scoped_session(session, workspace_id=ws):
                await repo.upsert_brand(
                    session,
                    ws,
                    slug=meta.slug,
                    display_name=meta.display_name,
                    parent_slug=meta.parent,
                    tagline=meta.tagline,
                    status="active",
                )
            counts[ws] = counts.get(ws, 0) + 1
    return counts


async def bootstrap() -> None:
    """Idempotently seed the canonical DB from config + the file volume.

    Guarded: any database problem is logged and skipped so boot never fails on
    it. Safe to run on every container start.
    """
    try:
        ws_id, slug = _bootstrap_workspace()
        if ws_id is None:
            _log.info(
                "tenancy bootstrap skipped: BRAND_OPS_BOOTSTRAP_WORKSPACE_ID unset"
            )
            return
        await ensure_workspace(ws_id, slug)
        counts = await import_file_brands(ws_id)
        for entry in parse_allowlist(os.getenv("BRAND_OPS_MEMBER_ALLOWLIST", "")):
            try:
                await ensure_membership(**entry)
            except SQLAlchemyError as exc:
                _log.warning(
                    "allowlist member skipped (%s): %s",
                    entry.get("user_key"),
                    exc.__class__.__name__,
                )
        _log.info(
            "tenancy bootstrap: workspace %s (%s), brands indexed %s",
            ws_id,
            slug,
            counts,
        )
    except RuntimeError as exc:
        _log.warning("tenancy bootstrap skipped: %s", exc)
    except SQLAlchemyError as exc:
        _log.error("tenancy bootstrap failed (db error): %s", exc.__class__.__name__)
