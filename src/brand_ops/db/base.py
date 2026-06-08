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


@asynccontextmanager
async def workspace_session(
    session: AsyncSession, workspace_id: str
) -> AsyncIterator[AsyncSession]:
    """Scope a session's transaction to one workspace for row level security.

    Runs ``SET LOCAL app.current_workspace = :id`` so the value lives only for
    the current transaction. Every RLS policy filters on that setting, so the
    runtime role only ever sees rows for ``workspace_id``. The bound parameter
    is cast to text by Postgres and compared as a uuid in the policy, which
    keeps the workspace id from being interpolated into SQL.
    """
    async with session.begin():
        await session.execute(
            text("SET LOCAL app.current_workspace = :id"),
            {"id": str(workspace_id)},
        )
        yield session
