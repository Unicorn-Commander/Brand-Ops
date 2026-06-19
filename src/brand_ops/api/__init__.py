"""Brand-Ops REST API package.

A FastAPI surface over the single generation engine, one of the interfaces
(alongside the CLI and the MCP server) that all route through
``brand_ops.engine.generate_asset``. The application factory and the
module-level ``app`` live in ``brand_ops.api.app``.

This package intentionally does NOT eager-import ``.app``. ``.app`` imports
``mcp_http`` which imports ``mcp_server``, and ``mcp_server`` imports back into
this package (``.api.catalog`` and friends), so eager-importing ``.app`` here
closes that import cycle and breaks importing ``mcp_server`` first (the stdio
MCP entrypoint, ``python -m brand_ops.mcp_server``). Import the application as
the submodule instead: ``from brand_ops.api.app import app`` or
``uvicorn brand_ops.api.app:app`` (both already used everywhere).
"""
from __future__ import annotations
