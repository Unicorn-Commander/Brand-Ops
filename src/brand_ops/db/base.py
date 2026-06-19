"""Async SQLAlchemy 2.0 engine, sessionmaker, and the RLS scoping helper.

The engine is created lazily on first use, never at import time, so importing
this module (in tests, in alembic, in tooling) does not open a connection or
require a DATABASE_URL. The application gets sessions from
:func:`get_sessionmaker`, then wraps each unit of work in
:func:`workspace_session` so row level security is enforced per transaction.
"""
from __future__ import annotations

import logging
import os
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

_log = logging.getLogger("brand_ops.db.base")

# Pool and timeout knobs, env-tunable but with safe production defaults.
#
#   pool_timeout    how long a caller waits for a free connection before failing
#                   (so an overloaded instance returns an error fast instead of
#                   hanging every request behind an exhausted pool).
#   pool_recycle    recycle a connection older than this many seconds, so a
#                   connection a proxy / Postgres idle-timeout has silently
#                   killed is replaced before it is handed out (pool_pre_ping
#                   already catches dead ones; recycle avoids the round trip).
#   statement_timeout_ms  a server-side cap (ms) on any single statement, set via
#                   asyncpg ``server_settings`` (see _connect_args), so a runaway
#                   query cannot pin a backend connection indefinitely. 0 disables.
_POOL_TIMEOUT_SEC = float(os.getenv("DB_POOL_TIMEOUT", "30"))
_POOL_RECYCLE_SEC = int(os.getenv("DB_POOL_RECYCLE", "1800"))
_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))


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
        _engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_timeout=_POOL_TIMEOUT_SEC,
            pool_recycle=_POOL_RECYCLE_SEC,
            future=True,
            connect_args=_connect_args(url),
        )
    return _engine


def _connect_args(url: str) -> dict:
    """Per-connection settings for the driver, including a server statement timeout.

    The statement timeout is applied via asyncpg's ``server_settings`` (a GUC the
    server enforces), so a single runaway query is killed server-side rather than
    holding a backend connection open forever. ``server_settings`` is asyncpg
    specific, so it is only set for an asyncpg URL; a different driver (or a
    timeout of 0) gets no extra connect args and behaves exactly as before.
    """
    if _STATEMENT_TIMEOUT_MS > 0 and url.startswith("postgresql+asyncpg"):
        return {"server_settings": {"statement_timeout": str(_STATEMENT_TIMEOUT_MS)}}
    return {}


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
    """Dispose the engine and drop the cached sessionmaker. Robust + idempotent.

    Closes the connection pool so a graceful shutdown drains its connections
    instead of severing them mid-flight, and clears the module caches so a later
    call rebuilds a fresh engine (the tests rely on this). Safe to call when no
    engine was ever built (a keyless run never opened one), and a failure to
    dispose is logged, not raised, so a shutdown path can call this without a
    try/except and a half-open pool can never block the process from exiting.
    """
    global _engine, _sessionmaker
    engine, _engine = _engine, None
    _sessionmaker = None
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:  # noqa: BLE001 - best-effort drain; never block shutdown
            _log.exception("engine dispose raised during shutdown; continuing")


async def shutdown() -> None:
    """Graceful-shutdown hook: drain the DB pool. Call from the app lifespan exit.

    A thin, intention-named wrapper over :func:`dispose_engine` so the app's
    shutdown path reads as ``await shutdown()`` and more drain steps can be added
    here later without touching the call site.
    """
    await dispose_engine()


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
