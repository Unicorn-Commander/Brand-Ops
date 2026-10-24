# Security

## Reporting a vulnerability

This repository does not publish a security email address. Report a vulnerability in private through GitHub Security Advisories:

[https://github.com/Unicorn-Commander/Brand-Ops/security](https://github.com/Unicorn-Commander/Brand-Ops/security)

Please do not open a public issue, pull request, or commit that includes exploit detail, a proof of concept against a host you do not own, or data from someone else's workspace.

In the report, include:

- the package version from `pyproject.toml` and the `version` field from `GET /health` (those strings can differ)
- the deployment shape (local, container, suite SSO) without private hostnames or credentials
- what an attacker would need, and what they would get
- whether you have a fix in mind

Give the maintainers time to investigate and ship a fix before any public write-up. This repository does not document a bug-bounty program or a disclosure window.

## What we expect from a deployment

- `AUTH_REQUIRED` defaults to true. Every `/api` route then requires a suite JWT or a Brand-Ops personal access token. Set `AUTH_REQUIRED=false` only on a machine you alone can reach. That mode serves every brand on disk to anyone who can open the port.
- HTTP MCP (`/mcp`) always requires a bearer, even when `AUTH_REQUIRED` is false. It is not mounted unless `MCP_HTTP_ENABLED` is set.
- The stdio MCP server exits immediately unless `BRAND_OPS_MCP_TOKEN` is set, and it re-checks that token on each call.
- Do not commit `.env`, database URLs with passwords, or provider keys.

## Scope

Brand-Ops is a self-hosted app. A report about a missing lock, a tenant seeing another tenant's brands, a spend path that runs without a credential, or an SSRF in intake or image-result fetching is in scope. A scan of a host you do not operate is not.
