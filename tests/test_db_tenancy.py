"""Postgres-backed tenancy tests: RLS isolation, membership, deny-by-default.

These exercise the real database path -- the resolver plus the row level
security policies -- which the file-based ``test_tenancy.py`` cannot. They are
SKIPPED unless a Postgres is wired: set ``BRAND_OPS_RUN_DB_TESTS=1`` and
``DATABASE_URL`` to the ``brand_ops_app`` (NOBYPASSRLS) role on a migrated
database, e.g.

    BRAND_OPS_RUN_DB_TESTS=1 \\
    DATABASE_URL=postgresql+asyncpg://brand_ops_app:apppw@127.0.0.1:55433/brand_ops_test \\
    uv run pytest tests/test_db_tenancy.py

Each test uses fresh uuids, so runs never collide and need no teardown.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

_RUN = os.getenv("BRAND_OPS_RUN_DB_TESTS", "").lower() in ("1", "true", "yes", "on")
_URL = os.getenv("DATABASE_URL", "")

pytestmark = [
    pytest.mark.skipif(
        not (_RUN and _URL),
        reason="needs Postgres: set BRAND_OPS_RUN_DB_TESTS=1 + DATABASE_URL (brand_ops_app role)",
    ),
    pytest.mark.asyncio,
]


@pytest_asyncio.fixture(autouse=True)
async def _dispose_engine_after_each():
    """Dispose the process-wide async engine after each test.

    pytest-asyncio runs each test in its own event loop, but the lazily-built
    engine caches pooled connections bound to the loop that first built it; a
    later test's loop would then find that connection's loop closed. Disposing
    after each test (in that test's still-open loop) makes the next test build a
    fresh engine on its own loop.
    """
    yield
    from brand_ops.db.base import dispose_engine

    await dispose_engine()


def _ids() -> tuple[str, str, str]:
    """Fresh (workspace_a, workspace_b, user_key) for one isolated test run."""
    return str(uuid.uuid4()), str(uuid.uuid4()), f"user-{uuid.uuid4()}"


async def _seed(ws1: str, ws2: str, user: str) -> None:
    """Two workspaces, a brand in each, and ``user`` a member of ws1 only."""
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, scoped_session
    from brand_ops.db.bootstrap import ensure_membership, ensure_workspace

    await ensure_workspace(ws1, f"ws-{ws1[:8]}")
    await ensure_workspace(ws2, f"ws-{ws2[:8]}")
    await ensure_membership(
        user_key=user, workspace_id=ws1, role="owner", is_default=True
    )
    sm = get_sessionmaker()
    async with sm() as session:
        async with scoped_session(session, workspace_id=ws1):
            await repo.upsert_brand(session, ws1, slug="brand-a", display_name="A")
        async with scoped_session(session, workspace_id=ws2):
            await repo.upsert_brand(session, ws2, slug="brand-b", display_name="B")


async def test_resolver_member_sees_only_their_workspace() -> None:
    from brand_ops.db.tenancy import resolve_context

    ws1, ws2, user = _ids()
    await _seed(ws1, ws2, user)
    ctx = await resolve_context(user)
    assert ctx.workspace_id == ws1
    assert ctx.role == "owner"
    assert ctx.allowed_brand_slugs == frozenset({"brand-a"})
    assert [m.workspace_id for m in ctx.memberships] == [ws1]


async def test_resolver_non_member_request_falls_back_to_default() -> None:
    from brand_ops.db.tenancy import resolve_context

    ws1, ws2, user = _ids()
    await _seed(ws1, ws2, user)
    # The caller requests ws2, which they are NOT a member of: they fall back to
    # their default (ws1), never landing in ws2's data. (A stale switcher pick or
    # a tampered header is safe and self-healing -- they see their own brands.)
    ctx = await resolve_context(user, requested_workspace_id=ws2)
    assert ctx.workspace_id == ws1
    assert ctx.allowed_brand_slugs == frozenset({"brand-a"})


async def test_resolver_stranger_gets_nothing() -> None:
    from brand_ops.db.tenancy import resolve_context

    ctx = await resolve_context(f"stranger-{uuid.uuid4()}")
    assert ctx.workspace_id is None
    assert ctx.memberships == ()


async def test_rls_brand_isolation_between_workspaces() -> None:
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, scoped_session

    ws1, ws2, user = _ids()
    await _seed(ws1, ws2, user)
    sm = get_sessionmaker()
    async with sm() as session:
        async with scoped_session(session, workspace_id=ws1):
            assert await repo.list_brand_slugs(session, ws1) == frozenset({"brand-a"})
        async with scoped_session(session, workspace_id=ws2):
            assert await repo.list_brand_slugs(session, ws2) == frozenset({"brand-b"})


async def test_runtime_role_cannot_bypass_rls() -> None:
    # The connecting role MUST be NOBYPASSRLS or every isolation guarantee is
    # void (SUITE-IDENTITY S9.3 analog for Brand-Ops).
    from brand_ops.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            bypass = (
                await session.execute(
                    text("select rolbypassrls from pg_roles where rolname = current_user")
                )
            ).scalar()
    assert bypass is False


async def test_deny_by_default_no_scope_sees_no_brands() -> None:
    # With NO workspace GUC set, the hardened policy (NULLIF(...,'')) yields NULL,
    # so the brand table returns zero rows -- deny-by-default at the DB.
    from brand_ops.db.base import get_sessionmaker

    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            n = (await session.execute(text("select count(*) from brand"))).scalar()
    assert n == 0


async def test_membership_is_user_scoped_not_workspace_scoped() -> None:
    # A user enumerates their OWN memberships with only app.current_user_key set
    # (the 0002 fix). A different user sees none of them.
    from brand_ops.db import repository as repo
    from brand_ops.db.base import get_sessionmaker, scoped_session

    ws1, ws2, user = _ids()
    await _seed(ws1, ws2, user)
    sm = get_sessionmaker()
    async with sm() as session:
        async with scoped_session(session, user_key=user):
            mine = await repo.list_user_memberships(session, user)
        async with scoped_session(session, user_key="nobody-else"):
            theirs = await repo.list_user_memberships(session, "nobody-else")
    assert [str(r.workspace_id) for r in mine] == [ws1]
    assert list(theirs) == []
