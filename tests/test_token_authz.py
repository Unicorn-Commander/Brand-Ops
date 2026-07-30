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
