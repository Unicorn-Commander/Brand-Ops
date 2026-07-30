"""Who may mint a durable credential, and how much reach it may carry.

Three defects are pinned here.

1. ``POST /api/tokens`` had NO role gate. A viewer -- a read-only member who
   cannot write anything through the REST surface, and (since the MCP guard
   mirrors those gates) nothing through the tool surface either -- could mint a
   365-day headless credential. Minting is now role-gated, and the minter's role
   is recorded on the row and re-applied as a CAP on every use, so a token can
   never carry more reach than its minter had at mint time. Not even later: a
   token minted by a viewer stays a viewer's token after its owner is promoted.

2. BRIGADE -> PAT LAUNDERING. ``tokens.assert_can_mint`` allowed
   ``credential_kind == "brigade"`` outright and never consulted ``agent_id`` or
   the ``act`` claim, so a short-lived DELEGATED agent token could mint a 365-day
   PAT for the human it acts as -- surviving revocation of the delegation, and
   attributed to a human who never took the action. Refused now, on both issuers
   (a Keycloak token can carry ``act`` too).

3. DEAD CONTROL. PAT ``scopes`` were stored, carried onto the ``Principal``, and
   enforced nowhere. The field is gone; ``role``, which IS enforced, replaced it.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from brand_ops import brand_loader
from brand_ops import tokens as pat_tokens
from brand_ops.api import auth as auth_mod
from brand_ops.api import tokens as tokens_api
from brand_ops.api.app import create_app
from brand_ops.api.auth import Principal, require_principal
from brand_ops.db.tenancy import WorkspaceContext

WS_A = "019e50a3-d995-723f-ab66-0f765f92c0f4"


@pytest.fixture(autouse=True)
def _clean_scope() -> Iterator[None]:
    brand_loader.reset_workspace_scope()
    try:
        yield
    finally:
        brand_loader.reset_workspace_scope()


def _client(principal: Principal) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


def _sso(role: str | None, **kw) -> Principal:
    return Principal(
        uc_uid="user-a",
        issuer="keycloak",
        credential_kind="keycloak",
        workspace_id=WS_A,
        workspace_slug="ws-a",
        role=role,
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. The mint role gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "", None, "wizard"])
def test_a_viewer_cannot_mint_a_durable_credential(role) -> None:
    """The defect: a read-only member could self-issue a 365-day credential.

    An unrecognised or missing role is refused for the same reason it is refused
    everywhere else in this codebase -- it ranks 0, and indeterminate denies.
    """
    with _client(_sso(role)) as client:
        r = client.post("/api/tokens", json={"name": "mine", "expires_in_days": 365})
    assert r.status_code == 403, r.text
    assert "role in this workspace" in r.text


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
def test_a_member_and_up_passes_the_role_gate(role: str) -> None:
    """The gate must refuse the right callers, not all of them.

    No database is configured in the test suite, so the mint stops at the 503
    ``_require_db`` guard -- which is one step PAST the 403 the role gate would
    have raised. Reaching 503 is therefore the proof that the gate let them
    through, without needing Postgres.
    """
    with _client(_sso(role)) as client:
        r = client.post("/api/tokens", json={"name": "mine"})
    assert r.status_code == 503, r.text


def test_the_role_gate_uses_the_same_hierarchy_as_every_other_route() -> None:
    assert tokens_api._MIN_MINT_ROLE in auth_mod._ROLE_RANK
    assert auth_mod._ROLE_RANK[tokens_api._MIN_MINT_ROLE] > auth_mod._ROLE_RANK["viewer"]


def test_check_mint_role_is_fail_closed() -> None:
    for bad in (None, "", "viewer", "Viewer", "nonsense"):
        with pytest.raises(Exception) as exc:
            tokens_api.check_mint_role(_sso(bad))
        assert getattr(exc.value, "status_code", None) == 403
    tokens_api.check_mint_role(_sso("owner"))


# ---------------------------------------------------------------------------
# 1b. The role CAP: never wider than the minter, ever
# ---------------------------------------------------------------------------


def test_cap_role_takes_the_lower_of_live_and_mint_time() -> None:
    assert pat_tokens.cap_role("owner", "viewer") == "viewer"
    assert pat_tokens.cap_role("viewer", "owner") == "viewer"
    assert pat_tokens.cap_role("owner", "owner") == "owner"
    assert pat_tokens.cap_role("admin", "member") == "member"
    assert pat_tokens.cap_role("member", "admin") == "member"


def test_cap_role_is_fail_closed_on_an_unknown_or_missing_mint_role() -> None:
    """A row with no recorded role caps at the floor, it is not ignored."""
    assert pat_tokens.cap_role("owner", None) == "viewer"
    assert pat_tokens.cap_role("owner", "") == "viewer"
    assert pat_tokens.cap_role("owner", "wizard") == "viewer"
    # No workspace at all stays no workspace.
    assert pat_tokens.cap_role(None, "owner") is None


def _scoped(principal: Principal, monkeypatch: pytest.MonkeyPatch, *, live_role: str):
    async def _resolve(uc_uid, *, requested_workspace_id=None):
        return WorkspaceContext(
            workspace_id=WS_A,
            workspace_slug="ws-a",
            role=live_role,
            allowed_brand_slugs=frozenset({"magic-unicorn"}),
        )

    monkeypatch.setattr(auth_mod, "resolve_context", _resolve)
    return asyncio.run(auth_mod._scope_principal(principal, None, None))


def test_a_token_minted_by_a_viewer_stays_a_viewer_after_a_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reach a token carries is fixed at mint time, downward only.

    Without the cap, a credential issued while its owner was a viewer silently
    becomes an owner's credential the day that human is promoted -- a durable
    escalation nobody re-authorised.
    """
    pat = Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-1",
        pat_role="viewer",
    )
    scoped = _scoped(pat, monkeypatch, live_role="owner")
    assert scoped.workspace_id == WS_A
    assert scoped.role == "viewer"


def test_a_token_minted_by_an_owner_follows_a_demotion_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap narrows; the live membership still governs the upper bound."""
    pat = Principal(
        uc_uid="user-a",
        issuer="pat",
        credential_kind="pat",
        token_id="tok-1",
        pat_role="owner",
    )
    scoped = _scoped(pat, monkeypatch, live_role="viewer")
    assert scoped.role == "viewer"


def test_the_cap_does_not_touch_an_sso_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a PAT is capped: a human's own session keeps its live role."""
    scoped = _scoped(_sso("owner"), monkeypatch, live_role="owner")
    assert scoped.role == "owner"


def test_the_minted_role_is_taken_from_the_principal_not_the_body() -> None:
    """A client cannot ask for a token more powerful than its own session.

    ``role`` is absent from the mint request schema entirely, so a body that
    tries to set it is ignored rather than honoured.
    """
    assert "role" not in tokens_api.TokenCreate.model_fields
    body = tokens_api.TokenCreate(name="x", role="owner")  # type: ignore[call-arg]
    assert not hasattr(body, "role")


# ---------------------------------------------------------------------------
# 2. Brigade -> PAT laundering
# ---------------------------------------------------------------------------


def test_a_delegated_agent_credential_cannot_mint_a_pat() -> None:
    """The hole: 'brigade' was allowed and the act/agent claim was never read."""
    with pytest.raises(pat_tokens.PatMintForbidden) as exc:
        pat_tokens.assert_can_mint("brigade", delegated=True)
    assert "delegated" in str(exc.value)
    # A Keycloak token can carry ``act`` too; same answer.
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint("keycloak", delegated=True)
    # A first-person identity on either issuer still mints.
    pat_tokens.assert_can_mint("brigade")
    pat_tokens.assert_can_mint("keycloak")


def test_is_delegated_reads_both_the_derived_id_and_the_raw_claim() -> None:
    """Fail closed: a differently shaped ``act`` still counts as delegation."""
    assert pat_tokens.is_delegated(_sso("owner", agent_id="agent-7")) is True
    assert (
        pat_tokens.is_delegated(_sso("owner", claims={"act": {"sub": "agent-7"}}))
        is True
    )
    # An ``act`` the verifier could not reduce to an id must still be delegation.
    assert pat_tokens.is_delegated(_sso("owner", claims={"act": {"foo": "bar"}})) is True
    assert pat_tokens.is_delegated(_sso("owner")) is False
    assert pat_tokens.is_delegated(_sso("owner", claims={"sub": "user-a"})) is False


@pytest.mark.parametrize(
    "kw",
    [
        {"agent_id": "agent-7"},
        {"claims": {"act": {"sub": "agent-7"}}},
        {"claims": {"act": {"client_id": "brigade-worker"}}},
    ],
)
def test_the_mint_endpoint_refuses_a_delegated_caller_end_to_end(kw) -> None:
    """Over HTTP: an act-token minting a durable credential is 403, not 201."""
    principal = Principal(
        uc_uid="user-a",
        issuer="brigade",
        credential_kind="brigade",
        workspace_id=WS_A,
        role="owner",  # a delegation for a full owner is still refused
        **kw,
    )
    with _client(principal) as client:
        r = client.post("/api/tokens", json={"name": "laundered", "expires_in_days": 365})
    assert r.status_code == 403, r.text
    assert "delegated agent credential" in r.text


def test_a_first_person_brigade_caller_is_not_blocked() -> None:
    """Brigade proxies real humans too; only the DELEGATED shape is refused.

    503 (no database in the suite) is one step past the 403 the mint policy would
    have raised, so it proves the policy let this caller through.
    """
    principal = Principal(
        uc_uid="user-a",
        issuer="brigade",
        credential_kind="brigade",
        workspace_id=WS_A,
        role="owner",
        claims={"sub": "user-a"},
    )
    with _client(principal) as client:
        r = client.post("/api/tokens", json={"name": "mine"})
    assert r.status_code == 503, r.text


def test_the_older_mint_rules_still_hold() -> None:
    """The delegation rule is added to the existing ones, not instead of them."""
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint("pat")
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint(None, anonymous=True)
    with pytest.raises(pat_tokens.PatMintForbidden):
        pat_tokens.assert_can_mint(None)


# ---------------------------------------------------------------------------
# 3. The dead control is gone
# ---------------------------------------------------------------------------


def test_pat_scopes_are_gone_rather_than_shipped_unenforced() -> None:
    """A stored, carried, never-consulted scope list is worse than no field.

    A client sets it and believes it constrains the token. Nothing anywhere read
    it: ``agent_scopes_satisfied`` only gates DELEGATED agent tokens
    (``agent_id`` set), which a PAT never is. It is removed at every layer rather
    than left as a security control that does nothing.
    """
    from brand_ops.db.models import ApiToken

    assert "scopes" not in tokens_api.TokenCreate.model_fields
    assert "scopes" not in tokens_api.TokenOut.model_fields
    assert not hasattr(pat_tokens.PatIdentity(token_id="t", user_key="u"), "scopes")
    assert "scopes" not in ApiToken.__table__.columns
    assert "role" in ApiToken.__table__.columns


def test_a_pat_principal_carries_no_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    """And the principal built from one is empty, not populated from a dead field."""

    async def _verify(raw):
        return pat_tokens.PatIdentity(token_id="tok-1", user_key="user-a", role="member")

    monkeypatch.setattr(pat_tokens, "verify_token", _verify)
    principal = asyncio.run(auth_mod.authenticate("brand_pat_" + "z" * 40))
    assert principal is not None
    assert principal.scopes == ()
    assert principal.pat_role == "member"


def test_the_two_role_hierarchies_cannot_drift() -> None:
    """``tokens`` duplicates the rank table (it must not import the HTTP layer).

    A duplicated constant is only safe if something fails when the copies
    diverge. This is that something.
    """
    assert pat_tokens._ROLE_RANK == auth_mod._ROLE_RANK


# ---------------------------------------------------------------------------
# 4. The role VALUE, end to end: minter -> row -> verified token -> enforcement
# ---------------------------------------------------------------------------
#
# Everything above pins the mint POLICY (who may mint) and the CAP ARITHMETIC
# (``cap_role`` given two roles). Neither touches the two lines where the role
# value is actually produced:
#
#   * ``api.tokens.create_token``   role=(principal.role or "").lower() or None
#   * ``tokens.verify_token``       role=row.role
#
# Replacing either with the constant ``"owner"`` left the whole suite green, and
# each one on its own hands every PAT owner powers -- the mint side forever
# (the row is written wrong), the verify side on every existing row.
# ``test_the_minted_role_is_taken_from_the_principal_not_the_body`` does not
# cover this: it only asserts ``role`` is absent from the request SCHEMA, and
# never looks at the value written.
#
# There is no Postgres in this suite, so the DATABASE is the seam -- the
# sessionmaker is replaced, and every line of the functions under test runs for
# real. The role value then travels the rest of the way through real code:
# row -> verify_token -> authenticate -> _scope_principal (cap) -> the MCP guard.


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Just enough AsyncSession for the two functions under test.

    Deliberately minimal: it stands in for Postgres and NOTHING else, so the
    mint's authorization, the row construction, the usability check, and the
    PatIdentity/Principal construction are all the real ones.
    """

    def __init__(self, row=None):
        self.row = row
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _FakeTxn()

    async def execute(self, stmt):
        return _FakeResult(self.row)

    def add(self, obj):
        self.added.append(obj)
        self.row = obj

    async def refresh(self, obj):
        # What the database would fill in on flush.
        import uuid as _uuid
        from datetime import UTC as _UTC
        from datetime import datetime as _dt

        if getattr(obj, "id", None) is None:
            obj.id = _uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = _dt.now(_UTC)


def _stub_db(monkeypatch: pytest.MonkeyPatch, session: _FakeSession, *modules):
    for mod in modules:
        monkeypatch.setattr(mod, "get_sessionmaker", lambda: (lambda: session))


@pytest.mark.parametrize("minter_role", ["member", "admin", "owner"])
def test_the_minted_row_records_the_minters_own_role(
    monkeypatch: pytest.MonkeyPatch, minter_role: str
) -> None:
    """MUTATION PIN: ``create_token`` writes the PRINCIPAL's role onto the row.

    Parametrised over every role that may mint, so a constant cannot satisfy it:
    writing ``"owner"`` unconditionally fails the member and admin cases. The
    assertion is on the ApiToken object actually handed to the session, not on a
    return value, because the row is what every later use reads.
    """
    from brand_ops.db import base as db_base

    session = _FakeSession()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    _stub_db(monkeypatch, session, tokens_api, db_base)

    body = tokens_api.TokenCreate(name="headless")
    # Mixed case on purpose: the row must be normalised, not echoed.
    out = asyncio.run(tokens_api.create_token(body, _sso(minter_role.upper())))

    assert len(session.added) == 1
    row = session.added[0]
    assert row.role == minter_role, f"row.role was {row.role!r}"
    assert out["role"] == minter_role
    assert row.user_key == "user-a"
    assert str(row.workspace_id) == WS_A
    # The plaintext is returned once and is not the thing stored.
    assert out["token"].startswith("brand_pat_")
    assert row.token_hash == pat_tokens.hash_token(out["token"])
    assert out["token"] not in (row.token_hash, row.token_prefix)


def _row_with_role(role: str | None, raw: str):
    from brand_ops.db.models import ApiToken

    return ApiToken(
        user_key="user-a",
        name="headless",
        token_hash=pat_tokens.hash_token(raw),
        token_prefix=pat_tokens.display_prefix(raw),
        role=role,
        workspace_id=None,
        brand_slug=None,
        expires_at=None,
        revoked_at=None,
    )


@pytest.mark.parametrize("stored_role", ["viewer", "member", "admin", "owner"])
def test_verify_token_returns_the_role_stored_on_the_row(
    monkeypatch: pytest.MonkeyPatch, stored_role: str
) -> None:
    """MUTATION PIN: ``verify_token`` reads ``row.role``, never a constant.

    Every rank is asserted, so substituting any single constant is red. This is
    the line that decides what an ALREADY MINTED token is worth on every call, so
    forging it here escalates every live PAT at once, retroactively.
    """
    from brand_ops.db import base as db_base

    raw, _, _ = pat_tokens.generate_token()
    session = _FakeSession(_row_with_role(stored_role, raw))
    _stub_db(monkeypatch, session, db_base)

    identity = asyncio.run(pat_tokens.verify_token(raw))
    assert identity is not None
    assert identity.role == stored_role
    # ...and it is carried onto the Principal the auth chain builds, unchanged.
    principal = asyncio.run(auth_mod.authenticate(raw))
    assert principal is not None
    assert principal.pat_role == stored_role


def test_a_viewer_minted_token_is_refused_an_owner_mcp_tool_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole chain, in one test, with the MCP guard as the far end.

    membership row (viewer) -> minted ApiToken row -> verify_token -> Principal
    -> _scope_principal (cap against a LIVE OWNER membership) -> the MCP
    dispatch. Every one of the three mutations breaks this: forge the mint role,
    forge the verified role, or forge the snapshot role, and the viewer's token
    clones a brand.
    """
    from brand_ops.api import mcp_auth
    from brand_ops.db import base as db_base
    from brand_ops.mcp_server import mcp

    # 1. A viewer... may not mint at all, which is the first gate. Mint as the
    #    lowest role that CAN, so the test is about the value, not the gate.
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    mint_session = _FakeSession()
    _stub_db(monkeypatch, mint_session, tokens_api, db_base)
    minted = asyncio.run(
        tokens_api.create_token(tokens_api.TokenCreate(name="headless"), _sso("member"))
    )
    row = mint_session.added[0]
    assert row.role == "member"

    # 2. That row is what the verifier reads back on every later call.
    verify_session = _FakeSession(row)
    _stub_db(monkeypatch, verify_session, db_base)

    # 3. The owner's LIVE membership is owner -- so only the recorded mint role
    #    can hold this token below owner. That is the escalation being pinned.
    async def _resolve(uc_uid, *, requested_workspace_id=None):
        return WorkspaceContext(
            workspace_id=WS_A,
            workspace_slug="ws-a",
            role="owner",
            allowed_brand_slugs=frozenset({"magic-unicorn"}),
        )

    async def _no_entitlements(raw, *, cache_key=None):
        return None

    monkeypatch.setattr(auth_mod, "resolve_context", _resolve)
    monkeypatch.setattr(auth_mod, "resolve_entitlements", _no_entitlements)

    mcp_auth.reset_mcp_principal()
    mcp_auth.set_stdio_credential(None)
    try:
        resolved = asyncio.run(mcp_auth.resolve_mcp_principal(minted["token"]))
        assert resolved is not None
        assert resolved.principal.role == "member", resolved.principal.role
        mcp_auth.set_mcp_principal(resolved)
        with pytest.raises(mcp_auth.MCPAuthzError) as exc:
            asyncio.run(
                mcp.call_tool(
                    "clone_brand",
                    {
                        "slug": "magic-unicorn",
                        "new_slug": "pat-clone",
                        "display_name": "PAT Clone",
                    },
                )
            )
        assert "owner role in this workspace" in str(exc.value)
        # The same token still gets the reads its role does carry.
        assert asyncio.run(mcp.call_tool("list_brands", {})) is not None
    finally:
        mcp_auth.reset_mcp_principal()
