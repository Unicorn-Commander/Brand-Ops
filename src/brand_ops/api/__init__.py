"""Brand-Ops REST API package.

A FastAPI surface over the single generation engine, one of the interfaces
(alongside the CLI and the MCP server) that all route through
``brand_ops.engine.generate_asset``. The application factory and the
module-level ``app`` live in ``brand_ops.api.app``.
"""
from __future__ import annotations

from .app import app, create_app

__all__ = ("app", "create_app")
