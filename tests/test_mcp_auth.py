"""Every MCP surface refuses an unauthenticated, unidentified caller.

The gap this pins: Brand-Ops had two MCP transports over one FastMCP instance
with opposite postures. ``/mcp`` verified a bearer per request, but its
``MCP_AUTH_REQUIRED=false`` branch served every tenant's brands to anyone who
could reach the port. The stdio server (``python -m brand_ops.mcp_server``, the
one actually reached in production via ``docker exec``) had NO authentication
code path at all: tenancy came from an environment variable the operator
asserted, with ``allowed_brands=None`` -- every brand of every tenant, every
write tool, no identity.

What is asserted here, transport by transport:

  * HTTP: no bearer -> 401; a garbage, expired, or signature-tampered token ->
    401; a real RS256 token signed by the realm's JWKS key -> accepted; a valid
    token whose owner has no membership -> 401 (never an unscoped session).
  * stdio: the entrypoint refuses to start without ``BRAND_OPS_MCP_TOKEN``, and a
    credential that does not resolve refuses each tool call.
  * Both: the FastMCP dispatch itself refuses a call that carries no principal,
    and a principal cannot reach a brand outside its workspace through a tool.
  * The PAT primitive: prefix, sha256-at-rest, expiry/revocation, fail-closed
    verification, workspace/brand bindings that only narrow, and the
    a-PAT-cannot-mint-a-PAT rule.

The RSA tests use a real generated key and real signatures, with only the JWKS
FETCH stubbed -- so they exercise jose's actual signature/issuer/expiry checking
rather than a mock of it. No network and no database is touched anywhere here.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk as jose_jwk
from jose import jwt

# brand_ops.api.app must be imported before mcp_server: app -> mcp_http ->
# mcp_server, and mcp_server imports back into brand_ops.api. Importing the app
# first closes the cycle (same note as tests/test_mcp.py).
from brand_ops.api.app import create_app  # isort: skip
from brand_ops import brand_loader
from brand_ops import tokens as pat_tokens
from brand_ops.api import auth as auth_mod
from brand_ops.api import mcp_auth
from brand_ops.api.auth import Principal
from brand_ops.db.tenancy import WorkspaceContext
from brand_ops.mcp_server import mcp

WS_A = "019e50a3-d995-723f-ab66-0f765f92c0f4"
WS_B = "019e50a3-d995-723f-ab66-0f765f92c0ff"
KID = "test-signing-key"


# ---------------------------------------------------------------------------
# Fixtures: clean scope, clean principal, no stdio credential
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """No scope, no principal, and no stdio credential leaks between tests."""
    brand_loader.reset_workspace_scope()
    mcp_auth.reset_mcp_principal()
    mcp_auth.set_stdio_credential(None)
    auth_mod._reset_caches_for_tests()
    try:
        yield
    finally:
        brand_loader.reset_workspace_scope()
        mcp_auth.reset_mcp_principal()
        mcp_auth.set_stdio_credential(None)
        auth_mod._reset_caches_for_tests()


@pytest.fixture(scope="module")
def rsa_key() -> tuple[str, dict]:
    """A real RSA keypair plus its public JWK, generated once for the module."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    public_jwk = jose_jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = KID
    return private_pem, public_jwk


def _issuer() -> str:
    from brand_ops.config import get_settings

    return str(get_settings().keycloak_realm_url).rstrip("/")


def _mint_jwt(private_pem: str, *, sub: str = "user-a", exp_delta: int = 3600) -> str:
    """A genuinely signed Keycloak-shaped RS256 bearer."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "uc_uid": sub,
        "iss": _issuer(),
        "aud": "brand-ops",
        "iat": now - 5,
        "exp": now + exp_delta,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})


def _serve_jwks(monkeypatch: pytest.MonkeyPatch, public_jwk: dict) -> None:
    """Stub only the JWKS FETCH. Signature verification stays real."""

    async def _fetch(url, cache, *, force=False):
        return {"keys": [public_jwk]}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", _fetch)


def _scope_and_read(principal: Principal, requested: str | None = None):
    """Run ``_scope_principal`` and read the scope it published, in one context.

    ``asyncio.run`` executes the coroutine in a COPY of the current context, so
    ContextVars set inside it are not visible to the caller afterwards -- the
    assertion has to happen on the inside.
    """

    async def _go():
        scoped = await auth_mod._scope_principal(principal, requested, None)
        return scoped, brand_loader.get_allowed_brands()

    return asyncio.run(_go())


def _serve_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace_id: str | None,
    brands: set[str],
    role: str = "owner",
) -> None:
    """Stub the tenancy resolver so no Postgres is needed."""

    async def _resolve(uc_uid, *, requested_workspace_id=None):
        if workspace_id is None:
            return WorkspaceContext()
        if requested_workspace_id and requested_workspace_id != workspace_id:
            # Mirrors the real resolver: a request for a workspace the caller is
            # not a member of falls back to their own default membership.
            return WorkspaceContext(
                workspace_id=workspace_id,
                workspace_slug="ws-a",
                role=role,
                allowed_brand_slugs=frozenset(brands),
            )
        return WorkspaceContext(
            workspace_id=workspace_id,
            workspace_slug="ws-a",
            role=role,
            allowed_brand_slugs=frozenset(brands),
        )

    monkeypatch.setattr(auth_mod, "resolve_context", _resolve)


# ---------------------------------------------------------------------------
# HTTP MCP: the bearer gate
# ---------------------------------------------------------------------------


def _mcp_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MCP_HTTP_ENABLED", "true")
    return TestClient(create_app())


def _ping(client: TestClient, token: str | None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        headers=headers,
    )


def test_http_mcp_refuses_missing_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, None)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-jwt",
        "a.b.c",
        "",
        "brand_pat_deadbeefdeadbeefdeadbeef",  # a well-formed PAT that does not exist
    ],
)
def test_http_mcp_refuses_unverifiable_credential(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Junk, a malformed JWT, and an unknown PAT are all 401 -- and the PAT case
    reaches the DB-less verifier, which must fail CLOSED rather than raise."""
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, bad)
    assert r.status_code == 401


def test_http_mcp_refuses_expired_token(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    expired = _mint_jwt(private_pem, exp_delta=-3600)
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, expired)
    assert r.status_code == 401


def test_http_mcp_refuses_tampered_signature(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    good = _mint_jwt(private_pem)
    head, payload, sig = good.split(".")
    # Flip one character of the signature; everything else is byte-identical.
    flipped = ("B" if sig[0] != "B" else "C") + sig[1:]
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, f"{head}.{payload}.{flipped}")
    assert r.status_code == 401


def test_http_mcp_refuses_tampered_claims(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """A re-encoded payload (privilege escalation attempt) fails the signature."""
    import base64
    import json

    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    head, payload, sig = _mint_jwt(private_pem).split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    claims["uc_uid"] = "somebody-else"
    forged = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    )
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, f"{head}.{forged}.{sig}")
    assert r.status_code == 401


def test_http_mcp_refuses_valid_token_without_membership(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """A perfectly valid bearer whose owner has no workspace is still refused.

    The REST surface can render "you have no workspace"; a tool call cannot, and
    serving it unscoped is exactly the hole being closed.
    """
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=None, brands=set())
    with _mcp_client(monkeypatch) as client:
        r = _ping(client, _mint_jwt(private_pem))
    assert r.status_code == 401


def test_http_mcp_accepts_valid_token(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """A real signature + a real membership is handed through to the MCP app.

    The inner protocol app is replaced by a recording stub: this asserts the auth
    wrapper DELEGATED (and with which principal), rather than asserting the
    absence of a 401, and it does not depend on the FastMCP session manager,
    which can only be started once per process.
    """
    from brand_ops.api import mcp_http

    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})

    seen: list[mcp_auth.ResolvedPrincipal | None] = []

    async def _stub_inner(scope, receive, send):
        seen.append(mcp_auth.get_mcp_principal())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    monkeypatch.setenv("MCP_HTTP_ENABLED", "true")
    app = create_app()
    monkeypatch.setattr(mcp_http, "_inner", _stub_inner)
    with TestClient(app) as client:
        r = _ping(client, _mint_jwt(private_pem))

    assert r.status_code == 200, r.text
    assert len(seen) == 1
    resolved = seen[0]
    assert resolved is not None, "the principal must be published for the call"
    assert resolved.workspace_id == WS_A
    assert resolved.allowed_brands == frozenset({"magic-unicorn"})


def test_http_mcp_does_not_delegate_when_the_credential_is_bad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal happens BEFORE the protocol app, so no tool can ever run."""
    from brand_ops.api import mcp_http

    reached: list[int] = []

    async def _stub_inner(scope, receive, send):  # pragma: no cover - must not run
        reached.append(1)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    monkeypatch.setenv("MCP_HTTP_ENABLED", "true")
    app = create_app()
    monkeypatch.setattr(mcp_http, "_inner", _stub_inner)
    with TestClient(app) as client:
        assert _ping(client, None).status_code == 401
        assert _ping(client, "garbage").status_code == 401
    assert reached == []


def test_resolve_mcp_principal_binds_the_callers_tenant(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """The resolved principal carries the caller's workspace and brand set."""
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    resolved = asyncio.run(mcp_auth.resolve_mcp_principal(_mint_jwt(private_pem)))
    assert resolved is not None
    assert resolved.workspace_id == WS_A
    assert resolved.allowed_brands == frozenset({"magic-unicorn"})
    assert resolved.principal.credential_kind == "keycloak"


# ---------------------------------------------------------------------------
# The per-call dispatch guard (shared by both transports)
# ---------------------------------------------------------------------------


def test_tool_call_without_a_principal_is_refused() -> None:
    """The FastMCP dispatch itself refuses when nothing authenticated the call.

    This is the assertion the stdio surface never had: before the guard, this
    same call returned every brand on disk.
    """
    with pytest.raises(mcp_auth.MCPAuthError):
        asyncio.run(mcp.call_tool("list_brands", {}))


def test_resource_and_prompt_reads_are_guarded_too() -> None:
    with pytest.raises(mcp_auth.MCPAuthError):
        asyncio.run(mcp.read_resource("brand-ops://brands"))
    with pytest.raises(mcp_auth.MCPAuthError):
        asyncio.run(mcp.get_prompt("new_brand_walkthrough", {"name": "x"}))


def test_guard_installation_is_idempotent() -> None:
    """Re-installing must not stack wrappers (each install adds a resolution)."""
    before = mcp.call_tool
    mcp_auth.install_mcp_guard(mcp)
    assert mcp.call_tool is before


# ---------------------------------------------------------------------------
# Tenant scoping through a tool call
# ---------------------------------------------------------------------------


@pytest.fixture
def two_tenant_kits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two workspaces on disk, arranged to exercise BOTH tenant boundaries.

    ``center-deep`` is deliberately placed inside workspace A's own subtree while
    being absent from A's DB-derived allow-list, so the allow-list gate is tested
    on a file that IS on the caller's path. ``majiks`` lives only in workspace B,
    so the per-workspace subtree is tested too.
    """
    repo_kits = brand_loader.DEFAULT_KITS_DIR
    root = tmp_path / "brand-kits"
    (root / WS_A).mkdir(parents=True)
    (root / WS_B).mkdir(parents=True)
    shutil.copytree(repo_kits / "magic-unicorn", root / WS_A / "magic-unicorn")
    shutil.copytree(repo_kits / "center-deep", root / WS_A / "center-deep")
    shutil.copytree(repo_kits / "majiks", root / WS_B / "majiks")
    monkeypatch.setattr(brand_loader, "DEFAULT_KITS_DIR", root)
    brand_loader.clear_brand_cache()
    return root


def test_principal_cannot_reach_another_tenants_brand(
    monkeypatch: pytest.MonkeyPatch,
    rsa_key: tuple[str, dict],
    two_tenant_kits: Path,
) -> None:
    """A workspace-A caller reaches only A's ENTITLED brands through a tool.

    Both tenant boundaries are exercised:

      * the DB-derived allow-list -- ``center-deep`` sits inside A's own kits
        subtree, so only the allow-list keeps it invisible;
      * the per-workspace subtree -- ``majiks`` lives under workspace B and is
        not on A's path at all.

    Reverting the dispatch guard makes this fail: the scope is published per
    call by the guard, so without it the tool runs unscoped and sees nothing at
    the workspace root.
    """
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})

    resolved = asyncio.run(mcp_auth.resolve_mcp_principal(_mint_jwt(private_pem)))
    assert resolved is not None
    mcp_auth.set_mcp_principal(resolved)

    listed = asyncio.run(mcp.call_tool("list_brands", {}))
    rendered = repr(listed)
    assert "magic-unicorn" in rendered
    assert "center-deep" not in rendered
    assert "majiks" not in rendered

    for foreign in ("center-deep", "majiks"):
        denied = repr(asyncio.run(mcp.call_tool("get_brand", {"slug": foreign})))
        assert "not found" in denied.lower(), denied
        # A denial must not leak the brand's contents in any form.
        assert "palette" not in denied.lower()


def test_workspace_header_cannot_select_a_foreign_workspace(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """``X-Workspace-Id`` is a REQUEST, never a grant: a non-membership falls
    back to the caller's own workspace, it does not select the asked-for one."""
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    resolved = asyncio.run(
        mcp_auth.resolve_mcp_principal(
            _mint_jwt(private_pem), requested_workspace_id=WS_B
        )
    )
    assert resolved is not None
    assert resolved.workspace_id == WS_A


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


def test_stdio_entrypoint_refuses_to_start_without_a_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``python -m brand_ops.mcp_server`` with no BRAND_OPS_MCP_TOKEN exits 2.

    Previously this started a fully unscoped, all-tenant, spend-bearing server.
    """
    import brand_ops.mcp_server as server

    monkeypatch.delenv("BRAND_OPS_MCP_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(server.mcp, "run", lambda: called.append(True))

    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 2
    assert called == [], "the server must not start without a credential"


def test_stdio_entrypoint_does_not_trust_the_workspace_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BRAND_OPS_MCP_WORKSPACE_ID alone no longer grants a session any tenancy.

    It used to be the ENTIRE tenancy mechanism for stdio -- an operator-asserted
    value with ``allowed_brands=None`` (all of that workspace's brands).
    """
    import brand_ops.mcp_server as server

    monkeypatch.delenv("BRAND_OPS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("BRAND_OPS_MCP_WORKSPACE_ID", WS_A)
    monkeypatch.setattr(server.mcp, "run", lambda: None)

    with pytest.raises(SystemExit):
        server.main()
    assert brand_loader.get_current_workspace_id() is None
    assert brand_loader.get_allowed_brands() is None  # nothing was published


def test_stdio_credential_that_does_not_resolve_refuses_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured-but-unverifiable credential refuses the tool call itself."""
    mcp_auth.set_stdio_credential("brand_pat_thisdoesnotexistanywhere0000")
    with pytest.raises(mcp_auth.MCPAuthError):
        asyncio.run(mcp.call_tool("list_brands", {}))


def test_stdio_credential_scopes_the_call_when_it_does_resolve(
    monkeypatch: pytest.MonkeyPatch,
    rsa_key: tuple[str, dict],
    two_tenant_kits: Path,
) -> None:
    """A valid stdio credential yields a workspace-scoped session, not a global one."""
    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    mcp_auth.set_stdio_credential(_mint_jwt(private_pem))

    listed = repr(asyncio.run(mcp.call_tool("list_brands", {})))
    assert "magic-unicorn" in listed
    assert "center-deep" not in listed


def test_stdio_session_revalidates_within_a_bounded_window() -> None:
    """The re-check TTL is short enough that a revocation lands on a live session."""
    assert 0 < mcp_auth.STDIO_REVALIDATE_SECONDS <= 300


# ---------------------------------------------------------------------------
# The PAT primitive
# ---------------------------------------------------------------------------


def test_pat_shape_and_hash_at_rest() -> None:
    raw, digest, prefix = pat_tokens.generate_token()
    assert raw.startswith("brand_pat_")
    assert len(raw) > 40
    assert digest == pat_tokens.hash_token(raw)
    assert len(digest) == 64 and digest == digest.lower()
    # The stored prefix is a display fragment, never enough to reconstruct.
    assert raw.startswith(prefix) and len(prefix) < len(raw)
    assert raw not in digest and digest != raw
    # Two mints never collide.
    assert pat_tokens.generate_token()[0] != raw


def test_pat_usability_is_fail_closed() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert pat_tokens.token_is_usable(revoked_at=None, expires_at=None) is True
    assert (
        pat_tokens.token_is_usable(revoked_at=None, expires_at=now + timedelta(days=1))
        is True
    )
    assert (
        pat_tokens.token_is_usable(revoked_at=None, expires_at=now - timedelta(seconds=1))
        is False
    )
    assert (
        pat_tokens.token_is_usable(revoked_at=now, expires_at=now + timedelta(days=1))
        is False
    )


def test_pat_verification_fails_closed_without_a_database() -> None:
    """No DB configured (or unreachable) must DENY, not raise and not allow."""
    raw, _, _ = pat_tokens.generate_token()
    assert asyncio.run(pat_tokens.verify_token(raw)) is None
    assert asyncio.run(auth_mod.authenticate(raw)) is None


def test_pat_shaped_credential_never_falls_through_to_the_jwt_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch is mutually exclusive: a PAT is never "tried as a JWT"."""
    calls: list[str] = []

    async def _kc(token):
        calls.append("keycloak")
        return Principal(uc_uid="nope", issuer="keycloak", credential_kind="keycloak")

    async def _br(token):
        calls.append("brigade")
        return Principal(uc_uid="nope", issuer="brigade", credential_kind="brigade")

    monkeypatch.setattr(auth_mod, "_verify_keycloak", _kc)
    monkeypatch.setattr(auth_mod, "_verify_brigade", _br)
    assert asyncio.run(auth_mod.authenticate("brand_pat_" + "z" * 40)) is None
    assert calls == []


def test_a_pat_cannot_mint_a_pat() -> None:
    """The core rule: a leaked token cannot roll itself past its own revocation."""
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint("pat")
    # Nor can an unauthenticated caller.
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint(None, anonymous=True)
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint(None)
    # An SSO identity may.
    pat_tokens.assert_can_mint("keycloak")
    pat_tokens.assert_can_mint("brigade")


def test_pat_mint_endpoint_refuses_a_pat_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end over HTTP: POST /api/tokens with a PAT principal is 403."""
    from brand_ops.api.auth import require_principal

    app = create_app()
    app.dependency_overrides[require_principal] = lambda: Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-1",
        workspace_id=WS_A,
        role="owner",
    )
    with TestClient(app) as client:
        r = client.post("/api/tokens", json={"name": "second"})
    assert r.status_code == 403
    assert "cannot mint" in r.text


def test_pat_mint_endpoint_refuses_the_anonymous_caller() -> None:
    """AUTH_REQUIRED off (the local/dev default) must not be able to mint."""
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/tokens", json={"name": "sneaky"})
    assert r.status_code == 403


def test_pat_workspace_binding_refuses_a_foreign_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace-bound PAT whose owner is not a member of it resolves to nothing.

    Removing the human's membership therefore revokes the token immediately,
    without touching the token row.
    """
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    bound = Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-1",
        pat_workspace_id=WS_B,  # bound to a workspace they are not in
    )
    scoped, allowed = _scope_and_read(bound)
    assert scoped.workspace_id is None
    assert allowed == frozenset()


def test_pat_brand_binding_only_narrows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A brand-bound PAT sees exactly one brand -- and cannot name one outside
    its owner's workspace to widen itself."""
    _serve_context(
        monkeypatch, workspace_id=WS_A, brands={"magic-unicorn", "majiks"}
    )
    narrowed = Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-1",
        pat_workspace_id=WS_A,
        pat_brand_slug="majiks",
    )
    _, allowed = _scope_and_read(narrowed)
    assert allowed == frozenset({"majiks"})

    widen = Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-2",
        pat_workspace_id=WS_A,
        pat_brand_slug="center-deep",  # not in this workspace
    )
    _, allowed = _scope_and_read(widen)
    assert allowed == frozenset()


def test_pat_scopes_do_not_carry_their_own_workspace_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unbound PAT still gets only its owner's memberships, never more."""
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})
    unbound = Principal(
        uc_uid="user-a", issuer="pat", credential_kind="pat", token_id="tok-3"
    )
    scoped, allowed = _scope_and_read(unbound, WS_B)
    assert scoped.workspace_id == WS_A
    assert allowed == frozenset({"magic-unicorn"})


# ---------------------------------------------------------------------------
# Entitlements: the MCP door consults the same signal the human surface does
# ---------------------------------------------------------------------------


def test_mcp_call_publishes_the_entitlement_signal(
    monkeypatch: pytest.MonkeyPatch, rsa_key: tuple[str, dict]
) -> None:
    """The generation gate's signal is resolved on the MCP path, not left UNKNOWN.

    Neither MCP surface used to call ``set_entitlement_scope`` at all, so the
    moment ``BRAND_OPS_ENTITLEMENT_ENFORCED`` is turned on the authed HTTP MCP
    would have failed CLOSED on every generate (UNKNOWN -> deny) while an unbound
    stdio session skipped the gate entirely (no workspace -> early return). Both
    now go through the same resolver the REST layer uses, and the resolved value
    is re-published on every individual call.
    """
    from brand_ops import entitlement

    private_pem, public_jwk = rsa_key
    _serve_jwks(monkeypatch, public_jwk)
    _serve_context(monkeypatch, workspace_id=WS_A, brands={"magic-unicorn"})

    async def _grants(token, *, cache_key=None):
        return ["brand_ops_access"]

    monkeypatch.setattr(auth_mod, "resolve_entitlements", _grants)

    async def _go():
        resolved = await mcp_auth.resolve_mcp_principal(_mint_jwt(private_pem))
        assert resolved is not None
        assert resolved.entitlements == ("brand_ops_access",)
        # And it is re-installed for the call itself, in a fresh context.
        entitlement.reset_entitlement_scope()
        mcp_auth.set_mcp_principal(resolved)
        await mcp_auth.current_call_principal()
        return entitlement.get_current_entitlement()

    assert asyncio.run(_go()) == ["brand_ops_access"]


def test_entitlement_enforcement_default_is_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring the check must NOT flip the enforcement flag's default to on.

    Enforcement stays operator-controlled via BRAND_OPS_ENTITLEMENT_ENFORCED; all
    this change does is make sure the signal is RESOLVED when it is turned on.
    """
    from brand_ops.config import get_settings

    monkeypatch.delenv("BRAND_OPS_ENTITLEMENT_ENFORCED", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().entitlement_enforced is False
    finally:
        get_settings.cache_clear()
