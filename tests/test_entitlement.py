"""Tests for the hosted-generation entitlement gate (paid-vs-free).

These are the safety contract for a feature shipped DORMANT: the gate must be a
complete no-op until an operator turns enforcement on, and every branch of the
enforce() decision must be pinned so the dev who wires the real tier source and
flips it on cannot be surprised.

Three layers, all Postgres-free and network-free (the gate is loop/DB-free by
design -- it reads only the flag, the allow-list, and the in-request ContextVar):

  1. interpret_claim: the pure claim -> ALLOW/DENY/UNKNOWN mapping.
  2. enforce(): the full decision matrix, including the production default
     (enforcement off => no-op even with a DENY claim set).
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
from brand_ops.entitlement import Entitlement, enforce, interpret_claim

# A workspace id is opaque; any stable string stands in for one in these tests.
_WS = "ws-11111111-1111-1111-1111-111111111111"


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
        "BRAND_OPS_ENTITLEMENT_CLAIM",
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
# interpret_claim: the pure ALLOW / DENY / UNKNOWN mapping
# ---------------------------------------------------------------------------


def test_interpret_none_is_unknown() -> None:
    # No claim at all -> UNKNOWN (no signal). The gate fails closed on this when
    # enforced, so a missing claim is never a free pass.
    assert interpret_claim(None) is Entitlement.UNKNOWN


def test_interpret_bool() -> None:
    # A plain brand_ops_hosted: true|false claim maps directly.
    assert interpret_claim(True) is Entitlement.ALLOW
    assert interpret_claim(False) is Entitlement.DENY


def test_interpret_tier_strings() -> None:
    # "free" (and the other explicit no-words) deny; any other non-empty tier
    # name permits. Case-insensitive and whitespace-tolerant.
    assert interpret_claim("free") is Entitlement.DENY
    assert interpret_claim("FREE") is Entitlement.DENY
    assert interpret_claim("  free  ") is Entitlement.DENY
    assert interpret_claim("none") is Entitlement.DENY
    assert interpret_claim("paid") is Entitlement.ALLOW
    assert interpret_claim("pro") is Entitlement.ALLOW
    assert interpret_claim("founder_friend") is Entitlement.ALLOW
    assert interpret_claim("vip_founder") is Entitlement.ALLOW
    # An empty / blank string carries no usable signal -> UNKNOWN.
    assert interpret_claim("") is Entitlement.UNKNOWN
    assert interpret_claim("   ") is Entitlement.UNKNOWN


def test_interpret_numeric_flag() -> None:
    # A 0/1 entitlement flag minted as an int.
    assert interpret_claim(1) is Entitlement.ALLOW
    assert interpret_claim(0) is Entitlement.DENY
    assert interpret_claim(2) is Entitlement.ALLOW


def test_interpret_dict_boolean_flag_wins() -> None:
    # A structured claim: a nested boolean hosted flag is honored first.
    assert interpret_claim({"hosted": True}) is Entitlement.ALLOW
    assert interpret_claim({"hosted": False}) is Entitlement.DENY
    assert interpret_claim({"hosted_generation": True}) is Entitlement.ALLOW
    assert interpret_claim({"allowed": False}) is Entitlement.DENY
    # The boolean flag wins over a tier string in the same dict.
    assert interpret_claim({"hosted": False, "tier": "pro"}) is Entitlement.DENY


def test_interpret_dict_tier_string() -> None:
    # No boolean flag -> fall back to a nested tier string.
    assert interpret_claim({"tier": "free"}) is Entitlement.DENY
    assert interpret_claim({"tier": "founder_friend"}) is Entitlement.ALLOW
    assert interpret_claim({"entitlement": "paid"}) is Entitlement.ALLOW
    assert interpret_claim({"plan": "free"}) is Entitlement.DENY
    # An empty / unrecognized dict has no usable signal -> UNKNOWN.
    assert interpret_claim({}) is Entitlement.UNKNOWN
    assert interpret_claim({"unrelated": "value"}) is Entitlement.UNKNOWN


def test_interpret_unknown_shapes_never_raise() -> None:
    # Anything we cannot map is UNKNOWN, never an exception (the gate is total).
    assert interpret_claim(["paid"]) is Entitlement.UNKNOWN
    assert interpret_claim(object()) is Entitlement.UNKNOWN


# ---------------------------------------------------------------------------
# enforce(): the decision matrix
# ---------------------------------------------------------------------------


def test_enforce_is_noop_when_flag_off_even_with_a_deny_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE production default: enforcement off => a complete no-op.

    With a DENY claim set and a real workspace, but BRAND_OPS_ENTITLEMENT_ENFORCED
    unset, enforce() must NOT raise. This is the guarantee that shipping the code
    changes nothing until an operator flips the switch: production behavior is
    byte-identical to before.
    """
    entitlement.set_entitlement_scope("free")  # a definite DENY
    enforce(_WS)  # must not raise


def test_enforce_is_noop_without_a_workspace_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced but unscoped (CLI / stdio MCP / tests / AUTH off) => no-op.

    An unscoped run has no tenant to entitle, mirroring the cost gate. Even with
    a DENY claim ambiently set, no workspace means no gate.
    """
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope("free")
    enforce(None)  # must not raise
    enforce("")  # empty string is also unscoped


def test_enforce_allows_allowlisted_workspace_even_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + workspace on the allow-list => allowed even with no claim.

    The allow-list bypass protects internal / dogfood / owner workspaces the
    moment enforcement is first enabled, regardless of the (here UNKNOWN) claim.
    """
    _enforce_on(monkeypatch)
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_ALLOWLIST", f"other-ws,{_WS},third-ws")
    get_settings.cache_clear()
    # No claim set at all -> UNKNOWN, which would otherwise fail closed.
    enforce(_WS)  # allow-listed -> must not raise


def test_enforce_allows_when_claim_is_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforced + the claim interprets as paid/ALLOW => allowed."""
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope("founder_friend")
    enforce(_WS)  # must not raise
    entitlement.set_entitlement_scope(True)
    enforce(_WS)  # a boolean true claim also allows


def test_enforce_denies_when_claim_is_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforced + the claim interprets as free/DENY => EntitlementDenied."""
    _enforce_on(monkeypatch)
    entitlement.set_entitlement_scope("free")
    with pytest.raises(EntitlementDenied):
        enforce(_WS)


def test_enforce_fails_closed_on_unknown_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforced + claim missing/UNKNOWN => EntitlementDenied (fail-closed).

    The deliberate opposite of the cost gate's soft posture: an enforced gate
    with no provable entitlement DENIES, because it protects shared-key spend.
    This is only ever reached once an operator has turned enforcement on.
    """
    _enforce_on(monkeypatch)
    # No claim published at all -> the ContextVar is None -> UNKNOWN.
    with pytest.raises(EntitlementDenied):
        enforce(_WS)
    # An unmappable claim value is also UNKNOWN and also denied.
    entitlement.set_entitlement_scope(["weird"])
    with pytest.raises(EntitlementDenied):
        enforce(_WS)


def test_entitlement_denied_is_a_backend_error() -> None:
    # The safety net: subclassing BackendError means the MCP and CLI paths map an
    # entitlement denial through their existing BackendError handling with no
    # per-surface edit; only the REST handler adds the precise 402.
    assert issubclass(EntitlementDenied, BackendError)
    err = EntitlementDenied("needs a paid plan")
    assert isinstance(err, BackendError)
    assert str(err) == "needs a paid plan"


def test_custom_claim_key_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured claim key is what auth reads; the signal here is the raw
    value. This proves a non-default BRAND_OPS_ENTITLEMENT_CLAIM still drives the
    same verdict (the gate reads the published value, not the key)."""
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_CLAIM", "tier")
    _enforce_on(monkeypatch)
    assert get_settings().entitlement_claim == "tier"
    entitlement.set_entitlement_scope("free")
    with pytest.raises(EntitlementDenied):
        enforce(_WS)


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
