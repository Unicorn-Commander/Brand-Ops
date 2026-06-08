"""FastAPI application factory for the Brand-Ops REST API.

This is one of the human-and-machine interfaces over the single generation
engine, alongside the CLI and the MCP server. It exposes brands, asset types,
dry-run prompt previews, live generation, and the on-disk asset catalog. It owns
no provider credentials and calls no provider SDK directly: every generation
goes through ``brand_ops.engine.generate_asset``, and a missing backend key is a
clean 422, not a 500.

Run it with::

    uvicorn brand_ops.api.app:app

CORS origins come from ``BRAND_OPS_CORS_ORIGINS`` (comma-separated). Auth is
optional and governed by ``AUTH_REQUIRED`` (see ``brand_ops.api.auth``).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from .auth import auth_required
from .routes import router
from .schemas import HealthOut


def _cors_origins() -> list[str]:
    """Parse the allowed CORS origins from the environment.

    ``BRAND_OPS_CORS_ORIGINS`` is a comma-separated list. The default is the
    production GUI host plus localhost dev ports. ``*`` is honored as an
    explicit single value for a fully open dev setup.
    """
    raw = os.getenv("BRAND_OPS_CORS_ORIGINS", "").strip()
    if not raw:
        return [
            "https://brand-ops.magicunicorn.dev",
            "http://localhost:5173",
            "http://localhost:3000",
        ]
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or ["*"]


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Brand-Ops API",
        version=__version__,
        summary="Brand identity and AI image-asset generation for the Unicorn Commander suite.",
    )

    origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health() -> HealthOut:
        """Liveness and configuration probe. Never touches a backend or the DB."""
        return HealthOut(
            status="ok",
            service="brand-ops",
            version=__version__,
            auth_required=auth_required(),
        )

    app.include_router(router)
    return app


# Module-level app for ``uvicorn brand_ops.api.app:app``.
app = create_app()
