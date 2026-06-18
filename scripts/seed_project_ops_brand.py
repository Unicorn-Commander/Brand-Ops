#!/usr/bin/env python3
"""Seed the Project-Ops brand row into the brand table.

The website lists brands from the Postgres ``brand`` table (an identity-only
row), NOT from the brand-kits files. ``seed_kits`` copies the kit files into the
volume on boot, and ``ingest_brand("project-ops")`` materializes the mascot mark
into ``imported/``, but neither writes the DB row -- so on a fresh deploy
Project-Ops would have its kit and logo on disk yet never appear on the site
until this row exists. This script writes it.

Idempotent and RLS-safe: it resolves the owning workspace by SLUG
(``magic-unicorn`` -- the dogfood/default tenant every other suite brand also
lives in; override with ``--workspace-slug``), creating the workspace row if it
is absent, then upserts the brand inside a ``workspace_session`` so the
row-level-security GUC (``app.current_workspace``) is set for the write. Re-runs
are a no-op update.

Run inside the backend container (it has DATABASE_URL and the package):

    docker exec brand-ops-backend python -m scripts.seed_project_ops_brand
    # or, from a checkout with DATABASE_URL exported:
    python scripts/seed_project_ops_brand.py

The brand IDENTITY (display name, parent, tagline) is read from
``brand-kits/project-ops/brand.yaml`` so this stays the single source of truth;
the equivalent raw SQL, for reference, is:

    -- as a role that can set app.current_workspace, with :ws = the
    -- magic-unicorn workspace.id:
    SET app.current_workspace = '<magic-unicorn workspace uuid>';
    INSERT INTO brand (workspace_id, slug, display_name, parent_slug, tagline, status)
    VALUES ('<ws>', 'project-ops', 'Project Ops', 'unicorn-commander',
            'Project delivery you self-host.', 'active')
    ON CONFLICT (workspace_id, slug) DO UPDATE
      SET display_name = EXCLUDED.display_name,
          parent_slug  = EXCLUDED.parent_slug,
          tagline      = EXCLUDED.tagline,
          status       = EXCLUDED.status;
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from brand_ops.brand_loader import load_brand
from brand_ops.db.base import get_sessionmaker, workspace_session
from brand_ops.db.models import Workspace
from brand_ops.db.repository import upsert_brand

SLUG = "project-ops"
DEFAULT_WORKSPACE_SLUG = "magic-unicorn"


async def _resolve_or_create_workspace(session, workspace_slug: str) -> str:
    """Return the workspace id for ``workspace_slug``, creating the row if missing.

    Workspace is not workspace-scoped by RLS in the same way the per-tenant
    tables are (it is the tenant), so this runs on a plain session. Resolving by
    slug keeps the script portable: it never hardcodes the uuid, which differs
    per deploy.
    """
    existing = await session.execute(
        select(Workspace).where(Workspace.slug == workspace_slug)
    )
    ws = existing.scalar_one_or_none()
    if ws is None:
        ws = Workspace(slug=workspace_slug)
        session.add(ws)
        await session.flush()
        print(f"created workspace {workspace_slug!r} -> {ws.id}")
    else:
        print(f"workspace {workspace_slug!r} -> {ws.id}")
    return str(ws.id)


async def seed(workspace_slug: str) -> None:
    brand = load_brand(SLUG)
    meta = brand.brand
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        # Resolve/create the workspace and commit it first, so its id is durable
        # before the RLS-scoped brand write references it.
        async with session.begin():
            workspace_id = await _resolve_or_create_workspace(session, workspace_slug)
        async with workspace_session(session, workspace_id):
            row = await upsert_brand(
                session,
                workspace_id,
                slug=meta.slug,
                display_name=meta.display_name,
                parent_slug=meta.parent,
                tagline=meta.tagline,
                status="active",
            )
            print(
                f"upserted brand {row.slug!r} (display={row.display_name!r}, "
                f"parent={row.parent_slug!r}, tagline={row.tagline!r}) "
                f"in workspace {workspace_id}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-slug",
        default=DEFAULT_WORKSPACE_SLUG,
        help=f"Owning workspace slug (default: {DEFAULT_WORKSPACE_SLUG}).",
    )
    args = parser.parse_args()
    asyncio.run(seed(args.workspace_slug))
    print("done.")


if __name__ == "__main__":
    main()
