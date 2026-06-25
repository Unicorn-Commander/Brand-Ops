"""Resolve a caller's suite entitlements from the Ops-Center endpoint.

The suite owns entitlement as a per-org fact. Brand-Ops learns it by forwarding
the caller's already-verified Keycloak bearer to the Ops-Center endpoint

    GET /api/v1/entitlements   ->   {"org_id", "tier", "entitlements": [...]}

and reading the returned ``entitlements`` list of canonical feature_keys. The gate
in :mod:`entitlement` then asks one question of that list: is Brand-Ops's
``feature_key`` (``brand_ops_access``) present?

This module is the only place that touches the network for entitlement. It is
deliberately split from the gate (``entitlement.enforce`` stays sync and
network-free, read at the generation chokepoint): the resolver runs ONCE per
request in the async auth layer, where the raw bearer is in hand, and publishes
its result into the request-scoped ContextVar the gate reads. The engine never
makes a per-generate network call.

Posture (matching the endpoint's documented contract):

  * Dormant by default. With no ``entitlement_endpoint_url`` configured the
    resolver returns ``None`` (UNKNOWN) and makes no network call, so the gate is
    a complete no-op. Setting the URL turns resolution on; ENFORCEMENT is a
    separate switch, so an operator can confirm the right grants resolve in the
    logs before flipping the gate to actually block.
  * Cached per caller for a short TTL (~60s), so a burst of generations is one
    endpoint call and an upgrade still takes effect within the TTL.
  * FAIL-SOFT for a known caller, FAIL-CLOSED for a never-seen one. On a
    successful response we trust it exactly (a feature_key absent is a true
    deny). On the endpoint being unreachable (timeout / 5xx / malformed) we reuse
    the last good grant if we have one (don't kick a paying customer on a blip),
    and return ``None`` = UNKNOWN if we have never seen this caller (which the
    gate, only when enforcement is on, fails closed on).

Never raises into the request: every failure path returns a list or ``None``.
"""
from __future__ import annotations

import logging
import time

import httpx

from .config import get_settings

_log = logging.getLogger("brand_ops.entitlements_client")

__all__ = ["resolve_entitlements"]

# A real-browser User-Agent: the entitlements endpoint sits on the same node
# domains behind Cloudflare, whose default WAF blocks the bare Python/httpx UA
# with error 1010 (the same reason api.auth pins one for the JWKS fetch).
_UA = "brand-ops/entitlements (+https://unicorncommander.ai)"

# In-process cache: (cache_key, org_id) -> {"entitlements": [...], "fetched_at": ts}.
# cache_key is the caller's uc_uid, so the grant is shared across that user's
# requests for the TTL. The entry doubles as the fail-soft "last good" grant: it
# is read past the TTL only when a refresh fails. Per-process and per-node, which
# is fine -- worst case is one extra endpoint call per worker per TTL.
_cache: dict[tuple[str, str | None], dict] = {}

# Bound the cache so a long-lived process serving many distinct callers cannot
# grow it without limit. Well above any single node's active-user count; reaching
# it just means a cold re-fetch for evicted callers.
_CACHE_MAX = 10_000


def _reset_cache_for_tests() -> None:
    """Test helper: clear the in-process entitlements cache."""
    _cache.clear()


def _maybe_prune(now: float, ttl: float) -> None:
    """Keep the cache bounded: drop expired entries, then the oldest if still over.

    Evicting the oldest fifth (rather than clearing everything) means a cap event
    never wipes every live last-good grant at once, which would turn an unrelated
    endpoint blip into a wave of fail-closed denials when enforcement is on.
    """
    if len(_cache) < _CACHE_MAX:
        return
    for key in [k for k, v in _cache.items() if (now - v["fetched_at"]) >= ttl]:
        del _cache[key]
    if len(_cache) < _CACHE_MAX:
        return
    oldest = sorted(_cache, key=lambda k: _cache[k]["fetched_at"])[: max(1, _CACHE_MAX // 5)]
    for key in oldest:
        del _cache[key]


async def _fetch_entitlements(
    url: str, token: str, org_id: str | None, timeout: float
) -> list[str]:
    """Call the endpoint and return its ``entitlements`` list. Raises on failure.

    The endpoint verifies the bearer itself (RS256 / JWKS, uchub issuers), so the
    only credential we send is the caller's own token. ``org_id`` is optional: omit
    it for the caller's primary org, or pass an Ops-Center org id (the endpoint
    membership-checks it and 403s a non-member) for an explicit active org.
    """
    params = {"org_id": org_id} if org_id else None
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": _UA}) as client:
        response = await client.get(
            url, params=params, headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict):
        raise ValueError("entitlements response is not a JSON object")
    entitlements = data.get("entitlements")
    if not isinstance(entitlements, list):
        raise ValueError("entitlements response missing an 'entitlements' array")
    # Defensive: keep only the string feature_keys; ignore any non-string members.
    return [item for item in entitlements if isinstance(item, str)]


async def resolve_entitlements(
    bearer_token: str | None, *, cache_key: str, org_id: str | None = None
) -> list[str] | None:
    """Resolve the caller's suite entitlement feature_keys, or ``None`` if unknown.

    Returns the list of granted feature_keys on success (possibly empty = a real
    "no grants"), the last good list when the endpoint is unreachable but this
    caller is cached (fail-soft), or ``None`` when the resolver is dormant / the
    caller has no token / the endpoint is unreachable with nothing cached (UNKNOWN,
    which the gate fails closed on only when enforcement is enabled). Never raises.
    """
    settings = get_settings()
    url = settings.entitlement_endpoint_url
    if not url or not bearer_token:
        # Resolver dormant (no endpoint configured) or no forwardable token: the
        # gate sees UNKNOWN and, since this is also the default state, stays inert.
        return None

    key = (cache_key, org_id)
    now = time.time()
    cached = _cache.get(key)
    if cached is not None and (now - cached["fetched_at"]) < settings.entitlement_cache_ttl_seconds:
        # Copy so a consumer mutating the returned list cannot corrupt the cache.
        return list(cached["entitlements"])

    try:
        entitlements = await _fetch_entitlements(
            url, bearer_token, org_id, settings.entitlement_http_timeout_seconds
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        if cached is not None:
            # Fail-soft: a transient endpoint problem must not kick a caller we
            # have already proven entitled within recent memory.
            _log.warning(
                "entitlements endpoint unreachable (%s); using last-good grant for %s",
                exc.__class__.__name__,
                cache_key,
            )
            return list(cached["entitlements"])
        # Fail-closed for a never-seen caller: no proof, so UNKNOWN.
        _log.warning(
            "entitlements endpoint unreachable (%s) and no cached grant for %s; "
            "entitlement UNKNOWN",
            exc.__class__.__name__,
            cache_key,
        )
        return None

    _maybe_prune(now, settings.entitlement_cache_ttl_seconds)
    _cache[key] = {"entitlements": entitlements, "fetched_at": now}
    _log.debug("resolved entitlements for %s: %s", cache_key, entitlements)
    # Copy so the request scope and the cache hold independent lists.
    return list(entitlements)
