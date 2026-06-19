# Brand-Ops

Brand identity and AI image-asset generation for the Unicorn Commander suite.
Logos, app icons, share images, podcast covers, and social cards for every
Magic Unicorn brand, generated on brand and on demand. MCP and agent first.

Anchor: your brands, generated on brand, on your machine.

## Status

See STATUS.md for the workstream tracker. Brand-Ops is **live on two nodes**:
the dogfood (`brand-ops.magicunicorn.dev`) and production
(`brand-ops.unicorncommander.ai`), both behind suite SSO. The brand schema and
loader, the prompt builder, the copy linter, the multi-backend image engine, the
CLI, the MCP server (stdio + HTTP, at full agent parity with the GUI/REST), the
REST API, the agent-first GUI, and live image + text generation are all shipped
and in use. Multi-tenancy is enforced
end to end: Postgres row level security **and** per-workspace on-disk storage
(`brand-kits/<workspace_id>/<slug>/`), so a new tenant is isolated in the
database and on the filesystem. Agents carry on-brand avatars and a bound TTS
voice; the GUI Crew page manages the per-brand cast. A 2026-06-18
production-readiness audit found the foundation strong (no critical/high
vulnerability) and scoped a tiered enterprise-hardening plan, tracked in
Project-Ops P-00073: backups + DR, per-tenant cost governance, rate limiting,
fail-secure AUTH default (Tier 1), then offboarding/export, observability, CI,
and audit-log hardening (Tier 2). Full MCP agent parity (Tier 1) shipped in
0.11.0.

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

On a deployed (multi-tenant) server, brand files live under a per-workspace
subtree so each tenant is isolated on disk: `brand-kits/<workspace_id>/<slug>/`.
Unscoped (the CLI, the stdio MCP, tests) uses the flat `brand-kits/<slug>/` root.
The single resolver is `brand_loader.current_kits_dir()`.

- `brand-kits/<ws>/<slug>/brand.yaml` brand definition (the authoring format)
- `brand-kits/<ws>/<slug>/refs/` reference images for composition
- `brand-kits/<ws>/<slug>/imported/` ingested existing assets (not tracked in git)
- `brand-kits/<ws>/<slug>/generated/` outputs (not tracked in git)
- `brand-kits/<ws>/<slug>/agents/<agent>/` per-brand agent identities (avatar + persona)
- `src/brand_ops/` the library, CLI, REST API, MCP server, agents, and DB layer
- `src/brand_ops/migrate_storage.py` the flat -> per-workspace migration (run once per volume)
- `web/` the agent-first GUI

To migrate an existing flat volume (non-destructive copy, verify, then cleanup):

```bash
python -m brand_ops.migrate_storage --dry-run   # report the plan
python -m brand_ops.migrate_storage             # copy beside the flat tree
python -m brand_ops.migrate_storage --cleanup   # remove the flat copies once verified
```

## Conventions

- uv, not pip.
- No em-dashes and no marketing-speak in any brand-facing copy. The copy linter
  enforces this on prompts and docs.
- Generated and imported assets and secrets stay out of git.

## Suite

Part of the Unicorn Commander suite. Brand-Ops owns its domain agents and
registers them with Unicorn Brigade so they are reachable from other apps, and
it surfaces in Ops-Center via the app registry.
