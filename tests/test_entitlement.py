"""Tests for the hosted-generation entitlement gate (paid-vs-free).

These are the safety contract for a feature shipped DORMANT: the gate must be a
complete no-op until an operator turns enforcement on, and every branch of the
enforce() decision must be pinned so the dev who flips it on cannot be surprised.

Three layers, all Postgres-free and network-free (the gate is loop/DB-free by
design -- it reads only the flag, the allow-list, the feature_key, and the
in-request ContextVar; the network resolution lives in entitlements_client and is
tested in test_entitlements_client.py):

  1. interpret_entitlements: the pure (grant list, feature_key) -> ALLOW/DENY/
     UNKNOWN mapping.
  2. enforce(): the full decision matrix, including the production default
     (enforcement off => no-op even with a DENY grant set).
  3. A route-level test that POST /api/generate returns 402 when the gate denies.

EntitlementDenied subclasses BackendError, so the MCP and CLI paths surface it
through their existing BackendError handling unchanged; only the REST handler
needs the dedicated 402 arm, which the route test exercises.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops import entitlement
from brand_ops.backends.base import BackendError, EntitlementDenied
from brand_ops.config import get_settings
from brand_ops.entitlement import Entitlement, enforce, interpret_entitlements

# A workspace id is opaque; any stable string stands in for one in these tests.
_WS = "ws-11111111-1111-1111-1111-111111111111"

# The canonical suite feature_key that grants Brand-Ops hosted generation (the
# config default). A grant list containing it -> ALLOW.
_KEY = "brand_ops_access"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Each test gets a clean entitlement env + a cleared settings cache + a reset
    ContextVar, so neither the ambient env nor an earlier test leaks in.

    ``get_settings`` is ``lru_cache``d, so the env must be set BEFORE the cache is
    populated; clearing it here (and after) makes every test read its own env.
    The entitlement signal ContextVar is reset so a DENY value set by one test
    never bleeds into the next.
    """
    for var in (
        "BRAND_OPS_ENTITLEMENT_ENFORCED",
        "BRAND_OPS_ENTITLEMENT_ALLOWLIST",
        "BRAND_OPS_ENTITLEMENT_URL",
        "BRAND_OPS_ENTITLEMENT_FEATURE_KEY",
        "BRAND_OPS_ENTITLEMENT_CACHE_TTL",
        "BRAND_OPS_ENTITLEMENT_HTTP_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    entitlement.reset_entitlement_scope()
    yield
    entitlement.reset_entitlement_scope()
    get_settings.cache_clear()


def _enforce_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn enforcement on and re-read settings (mirrors the operator flip)."""
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_ENFORCED", "1")
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# interpret_entitlements: the pure ALLOW / DENY / UNKNOWN mapping
# ---------------------------------------------------------------------------


def test_interpret_none_is_unknown() -> None:
    # Nothing resolved (no list) -> UNKNOWN. The gate fails closed on this when
    # enforced, so an unresolved entitlement is never a free pass.
    assert interpret_entitlements(None, _KEY) is Entitlement.UNKNOWN


def test_interpret_list_with_feature_key_allows() -> None:
    # The canonical grant: the feature_key is present in the org's list.
    assert interpret_entitlements([_KEY], _KEY) is Entitlement.ALLOW
    # Present among the other suite keys (the realistic full-suite response).
    grants = ["meeting_ops_access", _KEY, "contact_ops_access", "project_ops_access"]
    assert interpret_entitlements(grants, _KEY) is Entitlement.ALLOW


def test_interpret_list_without_feature_key_denies() -> None:
    # The suite answered, and Brand-Ops's key is absent -> a true DENY (a Free /
    # unentitled org), NOT unknown.
    assert interpret_entitlements(["meeting_ops_access"], _KEY) is Entitlement.DENY
    assert (
        interpret_entitlements(["contact_ops_access", "tax_ops_access"], _KEY)
        is Entitlement.DENY
    )


def test_interpret_empty_list_denies() -> None:
    # An explicit empty grant list is a definite "no grants" -> DENY, not UNKNOWN.
    assert interpret_entitlements([], _KEY) is Entitlement.DENY


def test_interpret_respects_a_custom_feature_key() -> None:
    # The check is membership against whatever key is configured.
    assert interpret_entitlements(["brand_ops_pro"], "brand_ops_pro") is Entitlement.ALLOW
    assert interpret_entitlements([_KEY], "brand_ops_pro") is Entitlement.DENY


def test_interpret_non_list_shapes_never_raise() -> None:
    # Anything that is not a list is UNKNOWN, never an exception (the gate is
    # total). A stray claim-shaped value from a misconfig must not be guessed at.
    assert interpret_entitlements("brand_ops_access", _KEY) is Entitlement.UNKNOWN
    assert interpret_entitlements({"entitlements": [_KEY]}, _KEY) is Entitlement.UNKNOWN
    assert interpret_entitlements(object(), _KEY) is Entitlement.UNKNOWN


# ---------------------------------------------------------------------------
# enforce(): the decision matrix
# ---------------------------------------------------------------------------


def test_enforce_is_noop_when_flag_off_even_with_a_deny_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE production default: enforcement off => a complete no-op.

    With a DENY grant set and a real workspace, but BRAND_OPS_ENTITLEMENT_ENFORCED
    unset, enforce() must NOT raise. This is the guarantee that shipping the code
    changes nothing until an operator flips the switch: production behavior is
    byte-identical to before.
    """
    entitlement.set_entitlement_scope(["meeting_ops_access"])  # a definite DENY
    enforce(_WS)  # must not raise


def test_enforce_is_noop_without_a_workspace_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced but unscoped (CLI / stdio MCP / tests / AUTH off) => no-op.

    An unscoped run has no tenant to entitle, mirroring the cost gate. Even with
    a DENY grant ambiently set, no workspace means no gate.
    """
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope(["meeting_ops_access"])
    enforce(None)  # must not raise
    enforce("")  # empty string is also unscoped


def test_enforce_allows_allowlisted_workspace_even_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + workspace on the allow-list => allowed even with no grant.

    The allow-list bypass protects internal / dogfood / owner workspaces the
    moment enforcement is first enabled, regardless of the (here UNKNOWN) grant.
    """
    _enforce_on(monkeypatch)
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_ALLOWLIST", f"other-ws,{_WS},third-ws")
    get_settings.cache_clear()
    # No grant resolved at all -> UNKNOWN, which would otherwise fail closed.
    enforce(_WS)  # allow-listed -> must not raise


def test_enforce_allows_when_grant_includes_feature_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + the resolved grant list includes brand_ops_access => allowed."""
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope([_KEY])
    enforce(_WS)  # must not raise
    entitlement.set_entitlement_scope(["meeting_ops_access", _KEY])
    enforce(_WS)  # present among siblings also allows


def test_enforce_denies_when_grant_lacks_feature_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + a resolved grant without brand_ops_access => EntitlementDenied."""
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope(["meeting_ops_access"])
    with pytest.raises(EntitlementDenied):
        enforce(_WS)
    # An explicit empty grant list is also a definite deny.
    entitlement.set_entitlement_scope([])
    with pytest.raises(EntitlementDenied):
        enforce(_WS)


def test_enforce_fails_closed_on_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + nothing resolved/UNKNOWN => EntitlementDenied (fail-closed).

    The deliberate opposite of the cost gate's soft posture: an enforced gate
    with no resolved entitlement DENIES, because it protects shared-key spend.
    This is only ever reached once an operator has turned enforcement on.
    """
    _enforce_on(monkeypatch)
    # No grant published at all -> the ContextVar is None -> UNKNOWN.
    with pytest.raises(EntitlementDenied):
        enforce(_WS)
    # An unmappable (non-list) value is also UNKNOWN and also denied.
    entitlement.set_entitlement_scope("brand_ops_access")  # a string, not a list
    with pytest.raises(EntitlementDenied):
        enforce(_WS)


def test_enforce_honors_a_custom_feature_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-default BRAND_OPS_ENTITLEMENT_FEATURE_KEY drives the verdict: the gate
    checks membership of the configured key, not the default."""
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_FEATURE_KEY", "brand_ops_pro")
    _enforce_on(monkeypatch)
    assert get_settings().entitlement_feature_key == "brand_ops_pro"
    # The default key present but not the configured one -> DENY.
    entitlement.set_entitlement_scope([_KEY])
    with pytest.raises(EntitlementDenied):
        enforce(_WS)
    # The configured key present -> allowed.
    entitlement.set_entitlement_scope(["brand_ops_pro"])
    enforce(_WS)  # must not raise


def test_entitlement_denied_is_a_backend_error() -> None:
    # The safety net: subclassing BackendError means the MCP and CLI paths map an
    # entitlement denial through their existing BackendError handling with no
    # per-surface edit; only the REST handler adds the precise 402.
    assert issubclass(EntitlementDenied, BackendError)
    err = EntitlementDenied("needs a paid plan")
    assert isinstance(err, BackendError)
    assert str(err) == "needs a paid plan"


# ---------------------------------------------------------------------------
# Route-level: POST /api/generate returns 402 when the gate denies
# ---------------------------------------------------------------------------


def test_generate_route_returns_402_when_entitlement_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The /generate except-chain maps EntitlementDenied -> 402 Payment Required.

    The gate fires inside engine.generate_asset (only when an operator enforces);
    here the engine is stubbed to raise EntitlementDenied, which is exactly what
    the enforced gate raises on a Free / unproven tier. The route must surface it
    as a 402 (before the generic BackendError arm, which would otherwise 502 it).
    Auth is off, so this exercises the handler arm directly without a token.
    """
    from brand_ops.api import routes
    from brand_ops.api.app import create_app

    monkeypatch.delenv("AUTH_REQUIRED", raising=False)

    def _deny(*args, **kwargs):
        raise EntitlementDenied(
            "hosted generation is part of a paid plan. The on-device tools and "
            "the card composer stay available on this plan; upgrade to turn on "
            "model-backed brand generation."
        )

    monkeypatch.setattr(routes.engine, "generate_asset", _deny)

    client = TestClient(create_app())
    resp = client.post("/api/generate", json={"brand": "magic-unicorn", "asset": "og_image"})

    assert resp.status_code == 402
    body = resp.json()
    # The RFC 7807 envelope: 402 status, the paid-plan copy preserved in detail.
    assert body["status"] == 402
    assert "paid plan" in body["detail"]


def test_generate_card_route_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keyless /generate-card path does not call generate_asset, so the
    entitlement gate never touches it: Free keeps composited cards.

    Even with enforcement on, a card request goes through engine.generate_card
    (no model, no spend, no gate). Stub it so no fonts/files are needed and
    assert the route does not 402.
    """
    from brand_ops.api import routes
    from brand_ops.api.app import create_app
    from brand_ops.engine import AssetOutcome
    from brand_ops.prompt_builder import BuiltPrompt

    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    _enforce_on(monkeypatch)

    built = BuiltPrompt(
        text="hi",
        asset_type="og_image",
        variant=None,
        backend="compose",
        size=(1200, 630),
        transparent=False,
        reference_images=[],
        warnings=[],
    )

    def _card(*args, **kwargs):
        # A composited card outcome with no result bytes is enough for the route
        # to shape a response; the point is that no entitlement gate ran.
        from brand_ops.backends.base import GenResult

        outcome = AssetOutcome(built=built, planned_path=routes.Path("x.png"), missing_refs=[])
        outcome.result = GenResult(
            images=[b"PNG"], mime="image/png", backend="compose", model="brand-ops-compose"
        )
        outcome.paths = []
        return outcome

    monkeypatch.setattr(routes.engine, "generate_card", _card)

    client = TestClient(create_app())
    resp = client.post(
        "/api/generate-card", json={"brand": "magic-unicorn", "asset": "og_image"}
    )
    assert resp.status_code != 402
