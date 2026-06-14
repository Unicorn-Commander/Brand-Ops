"""Async SQLAlchemy 2.0 engine, sessionmaker, and the RLS scoping helper.

The engine is created lazily on first use, never at import time, so importing
this module (in tests, in alembic, in tooling) does not open a connection or
require a DATABASE_URL. The application gets sessions from
:func:`get_sessionmaker`, then wraps each unit of work in
:func:`workspace_session` so row level security is enforced per transaction.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every Brand-Ops table."""


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, building it on first use.

    The URL is read from settings at call time, not at import time, so nothing
    connects until the application actually asks for the engine.
    """
    global _engine
    if _engine is None:
        url = get_settings().database_url
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. The database layer needs an "
                "asyncpg URL like postgresql+asyncpg://user:pass@host/db."
            )
        _engine = create_async_engine(url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async_sessionmaker, building it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Dispose the engine and drop the cached sessionmaker.

    Useful for clean shutdown and for tests that need a fresh engine.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def _apply_scope(
    session: AsyncSession,
    *,
    user_key: str | None,
    workspace_id: str | None,
) -> None:
    """SET the RLS GUCs for the current transaction, via ``set_config``.

    ``set_config(name, value, is_local=true)`` is used rather than ``SET LOCAL
    name = value`` because Postgres does not accept a bind parameter on a SET
    statement (``SET LOCAL x = $1`` is a syntax error); the function form binds
    the value, so the workspace id / user key is never interpolated into SQL. A
    local setting lives only for this transaction and reverts at commit, so a
    pooled connection never leaks one request's scope into the next.
    """
    if user_key is not None:
        await session.execute(
            text("SELECT set_config('app.current_user_key', :v, true)"),
            {"v": user_key},
        )
    if workspace_id is not None:
        await session.execute(
            text("SELECT set_config('app.current_workspace', :v, true)"),
            {"v": str(workspace_id)},
        )


@asynccontextmanager
async def scoped_session(
    session: AsyncSession,
    *,
    user_key: str | None = None,
    workspace_id: str | None = None,
) -> AsyncIterator[AsyncSession]:
    """Scope a transaction's row level security to a user and/or a workspace.

    Pass ``user_key`` to read or write the caller's own membership rows (the
    user-scoped ``workspace_member`` policy); pass ``workspace_id`` to read or
    write that workspace's data (the workspace-scoped policies on brand, asset,
    generation_job, brand_research_run); pass both when one unit of work needs
    each. Omitting both leaves every GUC unset, so every policy sees NULL and
    returns no rows -- deny-by-default. The bound values are never interpolated
    into SQL (see :func:`_apply_scope`).
    """
    async with session.begin():
        await _apply_scope(session, user_key=user_key, workspace_id=workspace_id)
        yield session


@asynccontextmanager
async def workspace_session(
    session: AsyncSession, workspace_id: str
) -> AsyncIterator[AsyncSession]:
    """Scope a session's transaction to one workspace for row level security.

    Thin wrapper over :func:`scoped_session` for the common single-workspace
    unit of work. Every RLS policy filters on ``app.current_workspace``, so the
    runtime role only ever sees rows for ``workspace_id``.
    """
    async with scoped_session(session, workspace_id=workspace_id) as scoped:
        yield scoped
