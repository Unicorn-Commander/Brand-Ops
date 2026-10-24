# Contributing to Brand-Ops

Brand-Ops is the brand kit and asset studio in the Unicorn Commander suite. Issues and pull requests go to [github.com/Unicorn-Commander/Brand-Ops](https://github.com/Unicorn-Commander/Brand-Ops).

## Set up

Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Node (the production web image uses 20.18.0).

```bash
uv sync --frozen --extra backends --extra dev
export AUTH_REQUIRED=false
uv run brand-ops list-brands
uv run uvicorn brand_ops.api.app:app --reload --host 127.0.0.1 --port 8000
```

`AUTH_REQUIRED` defaults to true. The API will not boot without a database and `BRAND_OPS_BOOTSTRAP_WORKSPACE_ID` unless you set `AUTH_REQUIRED=false` for local work. Do not use that setting on a shared host.

The GUI:

```bash
npm --prefix web install
npm --prefix web run dev
```

Vite proxies `/api` to `http://localhost:8000`.

## Checks

```bash
uv run pytest
uv run ruff check src tests
npm --prefix web test
npm --prefix web run typecheck
```

Ruff's settings are in `pyproject.toml`. The test suite forces `AUTH_REQUIRED=false` itself, so you do not export it for pytest.

## What belongs in a change

- Keep brand-facing copy free of em-dashes and marketing filler. The copy linter checks prompts.
- Do not commit `.env`, provider keys, generated images, or ingested asset bytes. Those trees are gitignored on purpose.
- Prefer `uv`, not pip.
- A new MCP tool or REST route needs the same role check as the surface it sits next to (viewer, member, admin, owner).
- If you change operator docs about `AUTH_REQUIRED`, the default in the code is on. Say `AUTH_REQUIRED=false` for the local opt-out. Do not tell people to unset the variable.

## Licence

By contributing, you agree that your contribution is licensed under the GNU Affero General Public License v3.0 or later, the same licence as the rest of the repository. See [LICENSE](LICENSE).
