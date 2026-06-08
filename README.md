# Brand-Ops

Brand identity and AI image-asset generation for the Unicorn Commander suite.
Logos, app icons, share images, podcast covers, and social cards for every
Magic Unicorn brand, generated on brand and on demand. MCP and agent first.

Anchor: your brands, generated on brand, on your machine.

## Status

See STATUS.md for the workstream tracker. The brand schema and loader, the
prompt builder, the copy linter, the multi-backend image engine, the CLI, the
MCP server, the REST API, the Postgres layer with per-workspace row level
security, and the agent-first GUI are built. The 177 existing brand assets are
ingested into the catalog and shown in the GUI. Live image generation is wired
behind provider keys (parked until keys land); upload and the Tagger/Refine
agents are in progress.

## 30-second start

One engine, three interfaces (CLI, MCP, REST) and a GUI over it.

```bash
# 1. list the seeded brands
uv run brand-ops list-brands

# 2. compose a prompt with no spend (dry run), reviewable before any generation
uv run brand-ops generate --brand magic-unicorn --asset og_image \
  --topic "investor update" --dry-run

# 3. bring the existing brand assets into the managed catalog (idempotent)
uv run brand-ops ingest                 # all known brands
uv run brand-ops ingest --brand majiks  # one brand

# 4. run the REST API (serves brands, asset types, dry-run previews, the
#    catalog, and the imported assets; no provider key needed)
uvicorn brand_ops.api.app:app --reload  # http://localhost:8000

# 5. run the GUI against the API (separate Vite app; proxies /api to :8000)
npm --prefix web install
npm --prefix web run dev                # http://localhost:5173
```

`ingest` copies each brand's known on-disk assets into
`brand-kits/<slug>/imported/<asset_type>/` and writes a catalog sidecar beside
each, so they appear in the GUI Library and serve through the API. Dry run
composes and prints the prompt without calling any backend, so prompts can be
reviewed before any spend.

## Web GUI

The `web/` directory is a Vite + React single-page app: Studio (compose and
preview), Library (browse the catalog, including the imported assets), Brands,
and Workflows. It calls the API with relative `/api` paths. In dev it proxies to
`http://localhost:8000`; in a deploy it is served as static files from the same
origin as the API (`npm --prefix web run build` -> `web/dist`). See
`docs/deploy.md`.

## Deploy

`docs/deploy.md` is the bigboy runbook (image build, `.env`, the `brand_ops`
Postgres db + the two roles + the alembic migration + RLS, the
`brand-ops-assets` Garage bucket, the Keycloak client, ingest, and a smoke
test). `docs/ops-center-registration.md` registers the app in Ops-Center. The
deploy files are `Dockerfile`, `deploy/docker-compose.yml` (generic), and
`deploy/docker-compose.bigboy.yml` (host-pinned, Traefik on
`brand-ops.magicunicorn.dev`).

## Layout

- `brand-kits/<slug>/brand.yaml` brand definition (the authoring format)
- `brand-kits/<slug>/refs/` reference images for composition
- `brand-kits/<slug>/imported/` ingested existing assets (not tracked in git)
- `brand-kits/<slug>/generated/` outputs (not tracked in git)
- `src/brand_ops/` the library, CLI, REST API, MCP server, agents, and DB layer
- `web/` the agent-first GUI

## Conventions

- uv, not pip.
- No em-dashes and no marketing-speak in any brand-facing copy. The copy linter
  enforces this on prompts and docs.
- Generated and imported assets and secrets stay out of git.

## Suite

Part of the Unicorn Commander suite. Brand-Ops owns its domain agents and
registers them with Unicorn Brigade so they are reachable from other apps, and
it surfaces in Ops-Center via the app registry.
