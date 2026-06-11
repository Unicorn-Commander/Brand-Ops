# Brand-Ops REST API image.
#
# A slim Python 3.12 base with uv for fast, reproducible installs. The image
# installs the package (without the provider SDK extra: the backend adapters
# lazy-import their SDKs and self-report unhealthy when one is absent, so the
# API runs and answers dry-run and catalog requests with no provider SDK at
# all). Add the [backends] extra at build time if live generation is needed in
# the container.
#
# This image runs ONLY the REST API (uvicorn brand_ops.api.app:app on :8000).
# The web GUI under web/ is a separate Vite single-page app: it is built with
# Node (npm --prefix web run build -> web/dist) and served as static files from
# the same public origin as the API, NOT from this image. See docs/deploy.md
# (section 8) for the two static-serving options. Keeping the GUI out of this
# image means a frontend change never rebuilds or restarts the API.
#
# The brand-kits tree is copied in (below), so the 177 imported brand assets and
# the brand definitions ship in the image and the catalog serves them with no
# external mount. Generated/imported outputs written at runtime live in the
# container's brand-kits tree; mount a volume there to persist them, and mirror
# to the brand-ops-assets Garage bucket via the GARAGE_S3_* env for durability.
FROM python:3.12-slim AS base

# uv: a single static binary copied from the official image. No pip bootstrap.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# Install dependencies first against just the manifest, so the layer caches
# across source-only changes. --no-install-project installs deps but not the
# package itself yet.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra backends

# Now the source, then install the package into the same environment.
COPY src ./src
# Bake the curated brands as a SEED. The live brand-kits dir (DEFAULT_KITS_DIR =
# /app/brand-kits) is a persistent volume on a server; seed_kits seeds it
# copy-if-missing on boot so generated assets + agent identities survive rebuilds.
COPY brand-kits ./brand-kits-seed
RUN uv sync --frozen --no-dev --extra backends

EXPOSE 8000

# Uvicorn serves the module-level app. One worker by default; scale with
# replicas behind Traefik rather than in-process workers so each worker keeps a
# clean process and the JWKS cache stays simple.
# Seed the live brand-kits volume copy-if-missing, then serve. `exec` hands the
# PID to uvicorn so signals (stop/restart) propagate cleanly.
CMD ["sh", "-c", "python -m brand_ops.seed_kits && exec uvicorn brand_ops.api.app:app --host 0.0.0.0 --port 8000"]
