"""Async database layer for Brand-Ops.

The catalog persists the same facts the output sidecar records (see
``brand_ops.output``), so an asset reads the same whether it came from a file
sidecar or from Postgres. Every workspace-scoped table is protected by row level
security: the runtime role cannot bypass it, and each transaction sets
``app.current_workspace`` via :func:`brand_ops.db.base.workspace_session`.
"""
from __future__ import annotations

from .base import Base, get_engine, get_sessionmaker, workspace_session

__all__ = ["Base", "get_engine", "get_sessionmaker", "workspace_session"]
