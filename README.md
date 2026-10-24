<p align="center">
  <img src="brand-kits/brand-ops/refs/colonel.png" width="160" alt="Brand-Ops mascot: a unicorn in a navy beret with a rainbow mane">
</p>

<h1 align="center">Brand-Ops</h1>

<p align="center">
  Machine-readable brand kits, and the studio that generates and checks on-brand assets.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License AGPL-3.0-or-later" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue"></a>
  <img alt="Package version 0.32.0" src="https://img.shields.io/badge/version-0.32.0-blue">
  <img alt="Python 3.12 or newer" src="https://img.shields.io/badge/python-3.12%2B-3776AB">
  <img alt="FastAPI and React" src="https://img.shields.io/badge/stack-FastAPI%20%2B%20React-009688">
  <img alt="36 MCP tools" src="https://img.shields.io/badge/MCP-36%20tools-black">
  <img alt="Self-hosted" src="https://img.shields.io/badge/self--hosted-yes-blue">
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#feature-tour">Features</a> ·
  <a href="#built-for-agents">Agents</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#part-of-unicorn-commander">Suite</a>
</p>

<p align="center">
  Public origin used by the web build:
  <a href="https://brand-ops.unicorncommander.ai">brand-ops.unicorncommander.ai</a>
</p>

<p align="center">
  <img src="docs/images/portfolio.png" width="880" alt="The Brand-Ops portfolio: every brand under command, with coverage and the agents on watch">
</p>

## What is Brand-Ops?

Brand-Ops is the system of record for a brand and the studio that produces its assets. It stores each brand's palette, type, voice, and rules as data, then lets a person or an agent generate assets against that record and check an image or a piece of copy for compliance. One engine sits behind a command line, a REST API, an MCP server, and a web app. It is self-hosted and multi-tenant, and it is part of [Unicorn Commander](https://unicorncommander.ai), which shares sign-in and can share inference and entitlements across the suite. Agents draft, route, and critique. An asset the checker marks `needs_review` does not fill a coverage gap until a person approves it. A generation that calls a model does not run until something asks for it.

## Highlights

- **The brand is data, not a slide deck.** `brand.yaml` holds colors, fonts, voice, forbidden phrases, and the mark. The CLI, the API, the MCP tools, and the GUI all read that file.
- **Look before you spend.** `build_prompt` and `brand-ops generate --dry-run` compose the prompt and print it. No image backend is called.
- **Text cards are drawn, not guessed.** Share images, social cards, and the launcher tile are composited from the brand's colors, bundled fonts, and mark, so the words are the words you passed and a second run is the same pixels.
- **Compliance is a check, then a human decision.** Copy is failed for a brand's forbidden phrases or an em-dash. Colors are compared with the palette. A `needs_review` asset stays a gap until someone approves it.
- **Agents do the drafting.** Iris (the `director`) talks in the Studio and routes the ask. Specialists research, take a brand in, pack assets, draw avatars, critique, tag, and refine. Every brand has a steward in the team view. The brand mark stands in as that steward's avatar until one is generated, and a created agent can be given a spoken voice.
- **Tenants are isolated twice.** Postgres row level security is forced on the tenancy tables, and brand files live under `brand-kits/<workspace_id>/<slug>/` when a workspace is in scope.
- **Several ways to make a picture.** Image calls can go to Gemini, OpenAI, Recraft, a local FLUX/ComfyUI server, OpenRouter, or a LiteLLM gateway. Five locked HTML templates paint from the brand's tokens and never call a model.
- **A link can unfurl on brand.** `share-meta` prints the Open Graph and Twitter tags for a brand's own site, and the same kit is on `GET /api/brands/{slug}/share-preview`.

## Feature tour

### Portfolio

The front door lists every brand you can see, with coverage of the asset types and the agents on watch. Opening a brand themes the workspace to that brand's palette and loads its display and body fonts.

<p align="center">
  <img src="docs/images/portfolio.png" width="880" alt="The Brand-Ops portfolio: every brand under command, with coverage and the agents on watch">
</p>

### Brand HQ

A brand's home screen is its mark, its grade, and its color tokens, drawn from the brand record rather than a separate theme file.

<p align="center">
  <img src="docs/images/brand-hq.png" width="880" alt="A brand HQ themed to the brand's own palette, with the mark, the brand grade, and the color tokens">
</p>

### Studio

The Studio is where a steward or Iris takes a plain-language ask: build a kit, make an OG image, or check assets against the rules. A pack request runs the `asset_pack` agent. A single asset can be previewed as a prompt before anything is generated.

<p align="center">
  <img src="docs/images/studio.png" width="880" alt="The Studio: a brand Steward ready to build the kit, make an OG image, or check assets against the rules">
</p>

### Brand book

Guidelines are rendered from the brand record: identity, color tokens, typography, voice, asset coverage, and a link-preview kit you can copy.

<p align="center">
  <img src="docs/images/brand-book.png" width="880" alt="The brand book: brand guidelines rendered from the machine-readable brand record">
</p>

### Launcher tile

The `launcher_tile` asset (512×512) is the square icon for an Ops-Center launcher tile. It is composed from the brand mark and palette, the same way the other deterministic cards are, so the icon does not depend on a model. How the app is registered in Ops-Center is written up in docs/ops-center-registration.md.

<p align="center">
  <img src="docs/images/launcher-tile.png" width="160" alt="The Brand-Ops Ops-Center launcher tile">
</p>

The same GUI also has a library (`/library` and `/b/:slug/assets`), a mark page (`/b/:slug/mark`), a grade page (`/standards` and `/b/:slug/grade`), a crew page for the per-brand cast (`/crew`), new-brand intake (`/brands/new`), and settings. `/workflows` and `/templates` redirect into the Studio.

## Built for agents

Brand-Ops exposes the same work the GUI does as MCP tools, so an agent can list brands, compose a prompt, generate or draw an asset, check compliance, and stand up a steward without clicking through the app.

**Transports.** Stdio is `python -m brand_ops.mcp_server`. It refuses to start unless `BRAND_OPS_MCP_TOKEN` is set, and it checks that credential against the database on each call. Streamable HTTP is mounted at `/mcp` only when `MCP_HTTP_ENABLED` is true. HTTP MCP always requires a bearer. Turning `AUTH_REQUIRED` off for local GUI work does not open `/mcp`. Discovery, when HTTP is on, is `GET /.well-known/mcps.json`.

**Auth.** A bearer is either a suite JWT (Keycloak `uchub` realm, or Brigade) or a Brand-Ops personal access token. Tokens use the prefix `brand_pat_` and are minted at `POST /api/tokens` by a member, admin, or owner. The anonymous local principal cannot mint one. Tools are allowed or refused with the same roles as the REST API: viewer, member, admin, owner.

**Surface.** 36 tools, 7 resources, and 3 prompts. The resources are `brand-ops://brands`, `brand-ops://asset-types`, `brand-ops://templates`, and per-brand guidelines, assets, tokens, and agents. The prompts are `launch_full_brand_kit`, `audit_brand_compliance`, and `stand_up_steward_agent`.

Tools you will actually call:

| Tool | What it does |
|---|---|
| `list_brands`, `get_brand` | Read the brands this credential can see, and one brand's identity |
| `build_prompt` | Compose an image prompt and return it. No backend call |
| `generate_asset` | Generate one asset. This spends money when a backend is configured |
| `generate_card` | Draw an OG image, social card, or launcher tile. No model |
| `generate_brand_kit` | Fill the asset types a brand is still missing |
| `check_compliance`, `get_brand_compliance` | Check copy or a file, or roll up a brand |
| `director_chat` | Talk to Iris and get a routed intent |
| `get_share_preview` | The Open Graph and Twitter tags for a brand's site |
| `intake_brand`, `new_brand`, `clone_brand` | Draft a brand, create one, or copy one |
| `approve_asset`, `transition_asset` | Move an asset through review. Approving is the human gate |

Eight agents are registered for Unicorn Brigade: `director` (Iris), `brand_research`, `brand_intake`, `asset_pack`, `avatar`, `brand_critic`, `tagger`, and `refine`. A ninth class, `generation_planner`, turns free text into a generation request and is not in that registry. There is no separate A2A endpoint. Brigade is the agent hook: the registry is the manifest Brigade ingests, and `brand_research` can call Brigade's research API. That call is unavailable until `BRIGADE_BASE_URL` and `BRIGADE_API_KEY` are both set. Agent chat falls back in persona when the LLM gateway has no key.

Stdio client (the token is verified in Postgres, so this path needs a database):

```json
{
  "mcpServers": {
    "brand-ops": {
      "command": "uv",
      "args": ["run", "python", "-m", "brand_ops.mcp_server"],
      "env": {
        "BRAND_OPS_MCP_TOKEN": "<token>",
        "DATABASE_URL": "postgresql+asyncpg://<user>:<password>@localhost:5432/brand_ops",
        "BRAND_OPS_MCP_WORKSPACE_ID": "<workspace-uuid>"
      }
    }
  }
}
```

HTTP client, after the API is up with `MCP_HTTP_ENABLED=true`:

```json
{
  "mcpServers": {
    "brand-ops": {
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer <token>"
      }
    }
  }
}
```

`BRAND_OPS_MCP_WORKSPACE_ID` only selects a workspace the token's owner already belongs to.

## Quick start

You can list brands, dry-run a prompt, draw a card, and open the GUI with no provider key and no suite login. Live image generation needs a provider key or a LiteLLM gateway. Until one is set, `POST /api/generate` returns HTTP 422. Agent chat answers 200 with an in-persona fallback when no LLM key is configured. `/health` is the liveness probe. `/readyz` stays not-ready until Postgres answers and an image backend reports itself configured, which this path does not set up.

The CLI imports Pillow. Pillow is the `backends` extra, so install that or `brand-ops` will not start.

### Docker

The API image serves the REST API on port 8000. It does not include the web GUI. `AUTH_REQUIRED` defaults to true, and a container that leaves it unset will refuse to boot unless `DATABASE_URL` and `BRAND_OPS_BOOTSTRAP_WORKSPACE_ID` are set. For a local look, turn that check off explicitly:

```bash
docker build -t brand-ops .
docker run --rm -p 8000:8000 -e AUTH_REQUIRED=false brand-ops
```

Then open `http://localhost:8000/docs` and `http://localhost:8000/api/brands`. The image copies the curated kits in and seeds them on boot, copy-if-missing, into `/app/brand-kits`.

The GUI image is `web/Dockerfile.prod`. The suite stacks wire it, Postgres, and Keycloak together:

```bash
docker compose -f deploy/docker-compose.unicorncommander.yml up -d --build
```

Run that from the repo root. It expects the suite's external Docker networks, Traefik, a root `.env`, `deploy/.env` (database passwords), and `deploy/.env.oauth2` (the oauth2-proxy). Those env files are not in git. The public host in that compose file is `brand-ops.unicorncommander.ai`, and sign-in is the shared Keycloak realm. `deploy/docker-compose.prod.yml` is the same shape for another environment. `deploy/docker-compose.yml` is the API alone and also expects those external networks. None of the compose files is a single-machine `docker compose up` with no surrounding suite.

With auth left on, the process also requires `DATABASE_URL` and `BRAND_OPS_BOOTSTRAP_WORKSPACE_ID`. Migrations run as the database owner (`alembic upgrade head`). The runtime role is not allowed to bypass row level security. See docs/deploy.md and docs/DEPLOY-TENANCY.md.

### Run from source

Requires Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node for the GUI. The production web image builds on Node 20.18.0.

```bash
uv sync --frozen --extra backends --extra dev
export AUTH_REQUIRED=false

uv run brand-ops list-brands

uv run brand-ops generate --brand magic-unicorn --asset og_image \
  --topic "investor update" --dry-run

uv run uvicorn brand_ops.api.app:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
npm --prefix web install
npm --prefix web run dev
```

The GUI is at `http://localhost:5173`. It calls relative `/api` paths, and the Vite dev server proxies those to `http://localhost:8000` ([web/vite.config.ts](web/vite.config.ts)).

Draw a card without a model. The compositor uses the brand mark when that file is in the kit:

```bash
uv run brand-ops card --brand brand-ops --asset og_image \
  --message "Every brand, under command." --out /tmp/og.png

uv run brand-ops card --brand magic-unicorn --asset social_card \
  --message "Hello from the studio."
```

`og_image` is 1200×630. `social_card` is 1080×1080. Omit `--out` and the social card is saved into that brand's catalog. The web app ships a default card at `web/public/og.png` and the tags in `web/index.html`. The absolute origin in those tags is `BRAND_OPS_SITE_URL` at build time, otherwise `https://brand-ops.unicorncommander.ai`.

Print the tags for a brand's own site. Anything you leave unset is marked as an example, not presented as the brand's real URL:

```bash
uv run brand-ops share-meta --brand magic-unicorn --url https://magicunicorn.tech
```

Bring a brand's known on-disk files into the managed catalog. This writes under `brand-kits/` and is safe to repeat:

```bash
uv run brand-ops ingest --brand majiks
uv run brand-ops ingest
```

`ingest` copies into `brand-kits/<slug>/imported/<asset_type>/` and writes a catalog sidecar next to each file. Generated and imported bytes are gitignored.

Other CLI commands: `backfill-compliance` stamps each catalog sidecar with the checker's lifecycle status.

### Configuration

Copy [.env.example](.env.example) to `.env`. Never commit `.env`. The table is names and meaning only.

| Variable | Meaning |
|---|---|
| `GEMINI_API_KEY` | Key for the Gemini image backend |
| `OPENAI_API_KEY` | Key for the OpenAI image backend |
| `RECRAFT_API_KEY` | Key for the Recraft backend, used when you want a native SVG path |
| `COMFYUI_URL` | Base URL of a ComfyUI server for the local FLUX backend |
| `BRIGADE_BASE_URL` | Unicorn Brigade base URL |
| `BRIGADE_API_KEY` | Key sent to Brigade |
| `GARAGE_S3_ENDPOINT` | S3 endpoint for the optional asset mirror |
| `GARAGE_S3_ACCESS_KEY` | Access key for that mirror |
| `GARAGE_S3_SECRET_KEY` | Secret key for that mirror |
| `GARAGE_S3_REGION` | Region string the S3 client sends |
| `GARAGE_S3_BUCKET` | Bucket that receives the mirror |
| `KEYCLOAK_REALM_URL` | Realm URL used to verify suite SSO tokens |
| `KEYCLOAK_CLIENT_ID` | OAuth client id for this app |
| `KEYCLOAK_CLIENT_SECRET` | OAuth client secret |
| `DATABASE_URL` | SQLAlchemy URL for the tenancy database (`postgresql+asyncpg://…`) |

These are not in `.env.example`, and a local or production boot depends on them:

| Variable | Meaning |
|---|---|
| `AUTH_REQUIRED` | Defaults to true. Every `/api` route then requires a bearer, and boot requires `DATABASE_URL` and `BRAND_OPS_BOOTSTRAP_WORKSPACE_ID`. Set `AUTH_REQUIRED=false` only for a local CLI, API, and GUI |
| `BRAND_OPS_BOOTSTRAP_WORKSPACE_ID` | Workspace id the seeded kits are bound to when auth is on |
| `MCP_HTTP_ENABLED` | Mount `/mcp` and `/.well-known/mcps.json`. Off unless set |
| `BRAND_OPS_MCP_TOKEN` | Bearer the stdio MCP server requires before it will start |
| `LLM_BASE_URL` | OpenAI-compatible base URL for agent chat |
| `LLM_API_KEY` | Key for that chat gateway. If empty, `OPENROUTER_API_KEY` is used |
| `LITELLM_BASE_URL` | Gateway root. When set, image calls can go there instead of a provider key in this process |
| `LITELLM_API_KEY` | Virtual key for that gateway |
| `BRAND_OPS_SELF_SERVE_SIGNUP` | Off unless set. When on, an authenticated user with no workspace may provision one |
| `BRAND_OPS_SITE_URL` | Absolute origin written into Open Graph tags at web build time |
| `BRAND_OPS_IMAGE_CAP_CENTS` | Per-workspace image spend cap in US cents. Unset or `0` means no cap |
| `BRAND_OPS_RATELIMIT` | Spend-route limit. Default `30/minute` |
| `SENTRY_DSN` | Optional error reporting. The SDK is the `sentry` extra and is not imported unless this is set |

Further knobs (entitlements, CORS, admin allow-list, retention, Orator TTS) are read in [src/brand_ops/config.py](src/brand_ops/config.py) and the API modules. Entitlement enforcement is off unless `BRAND_OPS_ENTITLEMENT_ENFORCED` is set. The feature key it checks defaults to `brand_ops_access`.

**Self-serve signup.** Off by default. An operator otherwise creates a workspace and grants members. With `BRAND_OPS_SELF_SERVE_SIGNUP=true`, an authenticated suite user who has no workspace gets a setup screen, and `POST /api/onboarding/provision` creates an owner and a default workspace. It is idempotent and audited. Brand-Ops does not bill. Plan and spend caps, when you use them, are the entitlement check and `BRAND_OPS_IMAGE_CAP_CENTS`. Letting a brand-new person register a `uchub` account is a Keycloak realm setting, not a Brand-Ops flag. See docs/DEPLOY-TENANCY.md.

## Architecture

```mermaid
flowchart LR
  subgraph clients [Clients]
    Web[Web GUI]
    CLI[CLI]
    MCPClient[MCP client]
  end
  Web --> API[FastAPI]
  CLI --> Lib[brand_ops library]
  MCPClient -->|stdio| MCP[MCP server]
  MCPClient -->|HTTP when enabled| API
  API --> Lib
  MCP --> Lib
  Lib --> Kits[brand-kits on disk]
  API --> DB[(Postgres with RLS)]
  Lib --> Img[Image backends]
  API --> KC[Keycloak]
  Lib --> Brig[Unicorn Brigade]
  API --> OC[Ops-Center entitlements]
```

| Piece | Where it lives |
|---|---|
| Web GUI | `web/`. Vite, React 18, React Router, TanStack Query. Routes: portfolio, brand HQ, mark, book, library, studio, grade, crew, new brand, settings |
| REST API | `src/brand_ops/api/`. FastAPI. With auth off and MCP HTTP off, the OpenAPI document lists 55 operations on 51 paths. `/readyz` and `/metrics` are mounted outside that schema |
| CLI | `brand-ops`, six commands: `list-brands`, `generate`, `card`, `share-meta`, `ingest`, `backfill-compliance` |
| MCP | `src/brand_ops/mcp_server.py`, plus the HTTP mount in `src/brand_ops/api/mcp_http.py` |
| Engine | Prompt builder, deterministic cards, compliance, catalog, ingest |
| Brand files | `brand-kits/<slug>/` when unscoped (CLI, tests, auth off). `brand-kits/<workspace_id>/<slug>/` when a workspace is in scope |
| Database | Postgres 16 in the compose files. Eight Alembic revisions under `alembic/versions/`. Row level security is enabled and forced |
| Image backends | `litellm`, `openrouter`, `gemini`, `openai`, `recraft`, `flux` |
| Agents | `src/brand_ops/agents/` and per-brand files under `brand-kits/<slug>/agents/` |
| Optional edges | Garage S3 mirror, Orator for voice preview, Redis for the rate limiter, Sentry |

On a fresh checkout the CLI lists 29 brands under `brand-kits/`. `_template/` is the authoring starter and is not one of them. Asset types are `logo` (2048²), `app_icon` (1024²), `launcher_tile` (512²), `og_image` (1200×630), `podcast_cover` (3000²), `social_card` (1080²), and `avatar` (1024²). Named generation packs are `core`, `social`, `logo`, `podcast`, and `launch`. Locked templates are `og_hero`, `social_square`, `social_story`, `quote_card`, and `feature_announcement`.

**What the server enforces**

- Auth fails closed. A missing `AUTH_REQUIRED` means on, not off.
- Spend-bearing POSTs are rate limited. The default is 30 requests per minute per caller (`BRAND_OPS_RATELIMIT`). `/health`, `/readyz`, `/metrics`, and reads are not limited. The store is in-memory unless `BRAND_OPS_RATELIMIT_REDIS_URL` is set (`ratelimit-redis` extra).
- Activity and admin audit logs are hash-chained. `GET /api/admin/workspaces/{workspace_id}/audit/verify` checks one workspace's chain.
- Uploaded rasters are opened and checked against the claimed format. Intake fetches and provider image URLs go through an SSRF guard (public http/https only).
- An admin can export a workspace (`GET /api/admin/workspaces/{workspace_id}/export`) and delete one. Deleting a workspace requires a matching confirmation.
- `/readyz` returns 503 when Postgres is unset or down, or when no image backend reports itself configured, so a load balancer can stop sending traffic. Shutdown closes the database pool.
- Prometheus metrics are on `/metrics` when `prometheus-fastapi-instrumentator` imports, which it does as a core dependency.

**Layout**

```text
brand-kits/<slug>/brand.yaml          brand definition
brand-kits/<slug>/refs/               reference images
brand-kits/<slug>/imported/           ingested assets, not in git
brand-kits/<slug>/generated/          model output, not in git
brand-kits/<slug>/agents/<agent>/     avatar and persona
src/brand_ops/                        library, CLI, API, MCP, agents, database
web/                                  GUI
deploy/                               compose files and the Postgres init script
```

The single path resolver is `brand_loader.current_kits_dir()`. To copy an existing flat tree into per-workspace directories (copy, check, then delete the flat copies):

```bash
python -m brand_ops.migrate_storage --dry-run
python -m brand_ops.migrate_storage
python -m brand_ops.migrate_storage --cleanup
```

**Conventions.** Use uv, not pip. Brand-facing copy does not use em-dashes or marketing filler. The copy linter enforces that on prompts. Do not commit generated assets, imported bytes, or secrets.

## Part of Unicorn Commander

| App | What it does |
|---|---|
| [Ops-Center](https://github.com/Unicorn-Commander/Ops-Center) | The control plane: tenants, billing, federation, API keys, model routing |
| [Unicorn-Brigade](https://github.com/Unicorn-Commander/Unicorn-Brigade) | The agent layer: agents, tools, memory, orchestration |
| [Unicorn-Stable](https://github.com/Unicorn-Commander/Unicorn-Stable) | Humans and agents in the same room: chat, voice, video |
| [Accounting-Ops](https://github.com/Unicorn-Commander/Accounting-Ops) | Agent-native accounting and books |
| [Contact-Ops](https://github.com/Unicorn-Commander/Contact-Ops) | The shared contact and identity graph |
| [Customer-Ops](https://github.com/Unicorn-Commander/Customer-Ops) | Customers, pipeline, quotes and outreach |
| [Email-Ops](https://github.com/Unicorn-Commander/Email-Ops) | Agent-drafted email with a human approval inbox |
| [Project-Ops](https://github.com/Unicorn-Commander/Project-Ops) | Projects, tasks, time and invoices |
| [Meeting-Ops](https://github.com/Unicorn-Commander/Meeting-Ops) | Recordings, transcripts, summaries and action items |
| [Knowledge-Ops](https://github.com/Unicorn-Commander/Knowledge-Ops) | The wiki and memory your agents ground on |
| [Security-Ops](https://github.com/Unicorn-Commander/Security-Ops) | Authorized security scanning with engagement and attestation gates |
| [Tax-Planning-Ops](https://github.com/Unicorn-Commander/Tax-Planning-Ops) | Tax plans, scenarios and statutory deadlines |
| **[Brand-Ops](https://github.com/Unicorn-Commander/Brand-Ops)** | **Brand kits, marks, voices and rules** |
| [Podcast-Ops](https://github.com/Unicorn-Commander/Podcast-Ops) | Episodes, audio QA and publishing |
| [UUDA](https://github.com/Unicorn-Commander/UUDA) | The device agent for your fleet |
| [fleet-map](https://github.com/Unicorn-Commander/fleet-map) | Fleet inventory and topology map |

Brand-Ops verifies suite JWTs against the shared Keycloak `uchub` realm, registers its agents with Unicorn Brigade and can call Brigade's research API, and reads the `brand_ops_access` entitlement from Ops-Center when `BRAND_OPS_ENTITLEMENT_URL` is set. Image and agent calls can be sent to a LiteLLM gateway instead of a provider key stored here. The `launcher_tile` asset is the icon Ops-Center shows for the app.

## Development

```bash
uv sync --frozen --extra backends --extra dev
uv run pytest
uv run ruff check src tests
npm --prefix web test
npm --prefix web run typecheck
```

`uv run pytest --collect-only -q` collects 1011 tests. Ruff is configured in `pyproject.toml` (line length 100, target Python 3.12). The web test script is Vitest (`npm --prefix web test`). `npm --prefix web run build` produces `web/dist`.

With a tenancy database configured, apply migrations as the owner role:

```bash
uv run alembic upgrade head
```

Backup and restore scripts are `scripts/backup-brand-ops.sh` and `scripts/restore-brand-ops.sh`. The notes for them are docs/BACKUP.md.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the local loop and what not to commit.

## Security

Report a vulnerability through [GitHub private vulnerability reporting](https://github.com/Unicorn-Commander/Brand-Ops/security) for this repository. This tree does not publish a security email address. Please do not file a public issue that includes exploit detail. See [SECURITY.md](SECURITY.md).

Do not run with `AUTH_REQUIRED=false` on a host other people can reach. That mode serves every brand on disk to anyone who can open the port.

## Licence

Brand-Ops is licensed under the GNU Affero General Public License v3.0 or later. See [LICENSE](LICENSE). If you run a modified version as a network service, the AGPL requires you to offer the corresponding source to its users.

<p align="center">
  Built by <a href="https://magicunicorn.tech">Magic Unicorn</a> · part of <a href="https://unicorncommander.ai">Unicorn Commander</a>
</p>
