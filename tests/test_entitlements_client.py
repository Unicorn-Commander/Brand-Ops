"""Tests for the Ops-Center entitlements resolver (entitlements_client).

This is the network half of the entitlement gate: it forwards the caller's
verified Keycloak bearer to GET /api/v1/entitlements, reads the org feature_keys,
caches them briefly, and -- crucially -- NEVER raises into the request. The
contract under test:

  * dormant by default (no endpoint URL -> None, no network call);
  * a successful response returns the grant list verbatim;
  * cached within the TTL (a burst is one call), refetched after it;
  * FAIL-SOFT for a known caller (last-good grant on a transient outage),
    FAIL-CLOSED (None) for a never-seen caller;
  * the parse path: bearer + optional org_id sent, entitlements array read,
    non-string members dropped, malformed / 5xx responses treated as failures.

The resolve-level tests stub the HTTP seam (_fetch_entitlements); the parse-level
tests drive the real _fetch_entitlements through an httpx MockTransport, so no
socket is ever opened.
"""
from __future__ import annotations

import httpx
import pytest

from brand_ops import entitlements_client
from brand_ops.config import get_settings
from brand_ops.entitlements_client import resolve_entitlements

_URL = "https://example.test/api/v1/entitlements"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clean entitlement env + cleared settings cache + cleared resolver cache."""
    for var in (
        "BRAND_OPS_ENTITLEMENT_URL",
        "BRAND_OPS_ENTITLEMENT_CACHE_TTL",
        "BRAND_OPS_ENTITLEMENT_HTTP_TIMEOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    entitlements_client._reset_cache_for_tests()
    yield
    entitlements_client._reset_cache_for_tests()
    get_settings.cache_clear()


def _set_url(monkeypatch: pytest.MonkeyPatch, *, ttl: str | None = None) -> None:
    monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_URL", _URL)
    if ttl is not None:
        monkeypatch.setenv("BRAND_OPS_ENTITLEMENT_CACHE_TTL", ttl)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# resolve_entitlements: dormancy, caching, fail posture (HTTP seam stubbed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dormant_without_url_returns_none_and_does_not_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No endpoint configured -> the resolver is a complete no-op (the prod default
    # until a node is wired). It must not even call the HTTP seam.
    calls = {"n": 0}

    async def _fetch(*args, **kwargs):
        calls["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    result = await resolve_entitlements("tok", cache_key="u1")
    assert result is None
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_no_token_returns_none_and_does_not_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # URL set but no forwardable token (e.g. a non-Keycloak caller): None, no call.
    _set_url(monkeypatch)
    calls = {"n": 0}

    async def _fetch(*args, **kwargs):
        calls["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    assert await resolve_entitlements("", cache_key="u1") is None
    assert await resolve_entitlements(None, cache_key="u1") is None
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_success_returns_grant_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_url(monkeypatch)
    grants = ["meeting_ops_access", "brand_ops_access"]

    async def _fetch(*args, **kwargs):
        return list(grants)

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    assert await resolve_entitlements("tok", cache_key="u1") == grants


@pytest.mark.asyncio
async def test_caches_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two resolves for the same caller inside the TTL = one endpoint call.
    _set_url(monkeypatch)  # default TTL 60s
    calls = {"n": 0}

    async def _fetch(*args, **kwargs):
        calls["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    first = await resolve_entitlements("tok", cache_key="u1")
    second = await resolve_entitlements("tok", cache_key="u1")
    assert first == second == ["brand_ops_access"]
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_refetches_after_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    # TTL 0 = every resolve is past the window, so each one refetches.
    _set_url(monkeypatch, ttl="0")
    calls = {"n": 0}

    async def _fetch(*args, **kwargs):
        calls["n"] += 1
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    await resolve_entitlements("tok", cache_key="u1")
    await resolve_entitlements("tok", cache_key="u1")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_fail_soft_returns_last_good_on_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A known caller keeps their last good grant when a refresh fails: don't kick a
    # paying customer on a blip. TTL 0 forces the second call onto the refetch path.
    _set_url(monkeypatch, ttl="0")
    state = {"fail": False}

    async def _fetch(*args, **kwargs):
        if state["fail"]:
            raise httpx.ReadTimeout("endpoint down")
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    first = await resolve_entitlements("tok", cache_key="u1")
    assert first == ["brand_ops_access"]
    state["fail"] = True
    second = await resolve_entitlements("tok", cache_key="u1")
    assert second == ["brand_ops_access"]  # last-good, not None


@pytest.mark.asyncio
async def test_fail_closed_returns_none_when_never_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No prior grant + endpoint down -> None = UNKNOWN, which the gate fails closed
    # on (only when enforcement is on).
    _set_url(monkeypatch)

    async def _fetch(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    assert await resolve_entitlements("tok", cache_key="u1") is None


@pytest.mark.asyncio
async def test_distinct_callers_do_not_share_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cache is keyed per caller, so one user's grant never answers another's.
    _set_url(monkeypatch)

    async def _fetch(url, token, org_id, timeout):
        return [token]  # echo the token so each caller gets a distinct list

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    a = await resolve_entitlements("tok-a", cache_key="user-a")
    b = await resolve_entitlements("tok-b", cache_key="user-b")
    assert a == ["tok-a"]
    assert b == ["tok-b"]


@pytest.mark.asyncio
async def test_cache_is_bounded_and_evicts_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    # Filling far past the cap keeps the cache bounded (no unbounded growth), and
    # eviction is oldest-first, so the most recent callers survive a cap event.
    _set_url(monkeypatch)
    monkeypatch.setattr(entitlements_client, "_CACHE_MAX", 10)

    async def _fetch(url, token, org_id, timeout):
        return [token]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    for i in range(40):
        await resolve_entitlements(f"tok-{i}", cache_key=f"user-{i}")

    assert len(entitlements_client._cache) <= entitlements_client._CACHE_MAX
    # The most recent caller is retained (oldest-first eviction, not a blunt clear).
    assert ("user-39", None) in entitlements_client._cache


@pytest.mark.asyncio
async def test_returned_list_is_a_copy_not_the_cached_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A consumer mutating the returned list must not corrupt the cache for the
    # caller's later requests within the TTL.
    _set_url(monkeypatch)

    async def _fetch(url, token, org_id, timeout):
        return ["brand_ops_access"]

    monkeypatch.setattr(entitlements_client, "_fetch_entitlements", _fetch)
    first = await resolve_entitlements("tok", cache_key="u1")
    first.append("tampered")  # mutate the returned list
    second = await resolve_entitlements("tok", cache_key="u1")  # served from cache
    assert second == ["brand_ops_access"]


# ---------------------------------------------------------------------------
# _fetch_entitlements: the parse path (real client, MockTransport, no socket)
# ---------------------------------------------------------------------------


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Bind the resolver's httpx.AsyncClient to a MockTransport for one test."""
    real = httpx.AsyncClient

    def _factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(entitlements_client.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_fetch_sends_bearer_and_org_id_and_parses_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "org_id": "org-1",
                "tier": "managed",
                "entitlements": ["meeting_ops_access", "brand_ops_access"],
            },
        )

    _mock_transport(monkeypatch, handler)
    out = await entitlements_client._fetch_entitlements(_URL, "the-token", "org-1", 4.0)
    assert out == ["meeting_ops_access", "brand_ops_access"]
    assert seen["auth"] == "Bearer the-token"
    assert seen["params"] == {"org_id": "org-1"}


@pytest.mark.asyncio
async def test_fetch_omits_org_id_param_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"entitlements": []})

    _mock_transport(monkeypatch, handler)
    out = await entitlements_client._fetch_entitlements(_URL, "tok", None, 4.0)
    assert out == []
    assert seen["params"] == {}  # primary-org call: no org_id query


@pytest.mark.asyncio
async def test_fetch_drops_non_string_members(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"entitlements": ["a", 1, None, "b", True]})

    _mock_transport(monkeypatch, handler)
    out = await entitlements_client._fetch_entitlements(_URL, "tok", None, 4.0)
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_fetch_raises_on_missing_array(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"org_id": "x", "tier": "free"})

    _mock_transport(monkeypatch, handler)
    with pytest.raises(ValueError):
        await entitlements_client._fetch_entitlements(_URL, "tok", None, 4.0)


@pytest.mark.asyncio
async def test_fetch_raises_on_non_object_body(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["brand_ops_access"])  # a bare array, not the envelope

    _mock_transport(monkeypatch, handler)
    with pytest.raises(ValueError):
        await entitlements_client._fetch_entitlements(_URL, "tok", None, 4.0)


@pytest.mark.asyncio
async def test_fetch_raises_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    _mock_transport(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await entitlements_client._fetch_entitlements(_URL, "tok", None, 4.0)
