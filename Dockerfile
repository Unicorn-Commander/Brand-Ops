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
# Base image pinned to an exact patch version so a rebuild is reproducible and a
# new base does not silently change the runtime.
# TODO: digest-pin (@sha256) under renovate
FROM python:3.12.7-slim AS base

# uv: a single static binary copied from the official image. No pip bootstrap.
# Pinned to an exact uv release rather than the floating latest tag.
# TODO: digest-pin (@sha256) under renovate
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# Non-root runtime user. Created up front so the chown below can assign it the
# app tree and the venv. The container does not need a login shell.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home-dir /app --no-create-home appuser \
    # The live brand-kits volume mounts here (DEFAULT_KITS_DIR = /app/brand-kits).
    # Create it now and hand it to appuser so seed_kits can write the seed copy
    # and the API can read and write generated assets when a volume is mounted.
    && mkdir -p /app/brand-kits

# Install dependencies first against just the manifest, so the layer caches
# across source-only changes. --no-install-project installs deps but not the
# package itself yet.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra backends

# Now the source, then install the package into the same environment.
COPY src ./src
# Migrations ship in the image so the deploy can run `alembic upgrade head` as
# the owner role from a one-off container (the runtime role is NOBYPASSRLS and
# cannot create the schema).
COPY alembic ./alembic
COPY alembic.ini ./
# Bake the curated brands as a SEED. The live brand-kits dir (DEFAULT_KITS_DIR =
# /app/brand-kits) is a persistent volume on a server; seed_kits seeds it
# copy-if-missing on boot so generated assets + agent identities survive rebuilds.
COPY brand-kits ./brand-kits-seed
RUN uv sync --frozen --no-dev --extra backends

# Hand the app tree and the venv to the non-root user. The venv is /usr/local
# (UV_PROJECT_ENVIRONMENT), so appuser must own it to import packages, read the
# bytecode written under UV_COMPILE_BYTECODE, and exec uvicorn. /app covers the
# source, alembic, the brand-kits seed, and the brand-kits mount path.
RUN chown -R appuser:appuser /app /usr/local

# Drop to the non-root user for the runtime. Everything above (package install,
# bytecode compile, seed copy) ran as root and is now owned by appuser.
USER appuser

EXPOSE 8000

# Uvicorn serves the module-level app. One worker by default; scale with
# replicas behind Traefik rather than in-process workers so each worker keeps a
# clean process and the JWKS cache stays simple.
# Seed the live brand-kits volume copy-if-missing, then serve. `exec` hands the
# PID to uvicorn so signals (stop/restart) propagate cleanly.
CMD ["sh", "-c", "python -m brand_ops.seed_kits && exec uvicorn brand_ops.api.app:app --host 0.0.0.0 --port 8000"]
