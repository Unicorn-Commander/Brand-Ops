"""Bearer-token authentication for the Brand-Ops REST API.

One validation path, two issuers, both optional:

  1. Keycloak (uchub realm) RS256 bearer. Signature, issuer, and expiry are
     verified against the realm JWKS, fetched once and cached. Audience is
     checked against the configured client id, accepting either an ``aud`` entry
     or a ``resource_access`` key (Keycloak stamps ``aud="account"`` on most
     tokens, so a strict aud-only check would reject valid cross-client tokens).
  2. Brigade-minted JWT. Brigade is the suite's federated MCP gateway; it mints
     short-lived tokens (EdDSA or RS256) that proxy a UC user's identity into
     member apps. It uses a different issuer, audience, and JWKS, so it gets its
     own verifier with its own cache. A Brigade token is tried only after the
     Keycloak path declines, mirroring the Accounting-Ops auth chain.

Auth is OPTIONAL. ``AUTH_REQUIRED`` (env, default false) gates the whole thing:
when false, every request resolves to an anonymous principal and no token is
required, so tests and local development work with no Keycloak or Brigade. When
true, a valid bearer from either issuer is required and anything else is 401.

This module reads its own env (AUTH_REQUIRED, BRIGADE_* , KEYCLOAK_AUDIENCE)
directly rather than extending the shared Settings model, which is owned by
another workstream. The Keycloak realm URL and client id come from the shared
``get_settings()`` so there is a single source for those.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from typing import Annotated, Any

import httpx
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    from jose import JWTError, jwt
except ImportError:  # pragma: no cover - python-jose is a hard dependency
    JWTError = Exception  # type: ignore[assignment]
    jwt = None  # type: ignore[assignment]

from ..activity import set_current_actor
from ..brand_loader import set_workspace_scope
from ..config import get_settings
from ..db.tenancy import resolve_context

_log = logging.getLogger("brand_ops.api.auth")

# HTTPBearer with auto_error off: we decide the failure mode (401 vs anonymous)
# based on AUTH_REQUIRED, so we must see a missing credential as None, not as an
# automatic 403 raised inside the security scheme.
_bearer = HTTPBearer(auto_error=False)

# Algorithms we will ever accept. Strict allowlist: never trust a token's own
# declared algorithm without checking it here. ``none`` is intentionally absent;
# HS* are absent because we share no symmetric secret with either issuer.
_ACCEPTED_ALGS = frozenset({"RS256", "EdDSA"})

# Small clock-skew tolerance for time-based claims.
_CLOCK_SKEW_SEC = 30


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """The validated caller.

    ``anonymous`` is the principal returned when AUTH_REQUIRED is false and no
    valid token was presented. ``uc_uid`` is the canonical Unicorn ecosystem
    user id (sub on a Keycloak token, the Brigade subject on a Brigade token).
    ``issuer`` records which path validated the token, for logging and audit.
    ``workspace_id`` is the caller's active workspace for this request (None when
    unscoped -- the anonymous local/CLI path -- or when the caller has no usable
    membership). ``role`` is the caller's canonical role in that workspace. Both
    are resolved by :func:`require_principal` from the caller's memberships, not
    carried on the token; the readable brand set is published to the
    ``brand_loader`` scope at the same time.
    """

    uc_uid: str
    agent_id: str | None = None
    scopes: tuple[str, ...] = ()
    issuer: str | None = None
    anonymous: bool = False
    workspace_id: str | None = None
    workspace_slug: str | None = None
    role: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)


def _candidate_workspace(claims: dict[str, Any], x_workspace_id: str | None) -> str | None:
    """The workspace the caller is asking to act in, by SUITE-IDENTITY D4 order.

    The ``X-Workspace-Id`` header (set by the GUI workspace switcher) wins; then
    the token's ``workspace_id`` claim (a Brigade token carries one). Whatever is
    returned is only a REQUEST -- the resolver honors it only if the caller is an
    active member of it (D5: membership gates). Returns None when neither is
    present, so the resolver falls to the caller's default membership. The
    deprecated ``org``/``organization`` claims are intentionally NOT read: the
    cross-app key is the ``workspace_id`` uuid, never a slug or org name.
    """
    if isinstance(x_workspace_id, str) and x_workspace_id.strip():
        return x_workspace_id.strip()
    claim = claims.get("workspace_id")
    if isinstance(claim, str) and claim.strip():
        return claim.strip()
    return None


ANONYMOUS = Principal(uc_uid="anonymous", anonymous=True, issuer=None)


# ---------------------------------------------------------------------------
# Settings read from the environment (this layer owns these knobs)
# ---------------------------------------------------------------------------


def auth_required() -> bool:
    """Whether a valid bearer token is required. Default false.

    Honors ``AUTH_REQUIRED`` with any truthy value (1, true, yes, on). Read at
    call time so a test can flip it with an env var without reimporting.
    """
    return os.getenv("AUTH_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")


def _keycloak_audience() -> str:
    """The audience a Keycloak token must carry to be accepted here.

    Defaults to the configured client id (brand-ops). Override with
    ``KEYCLOAK_AUDIENCE`` if a different resource id is minted into tokens.
    """
    return os.getenv("KEYCLOAK_AUDIENCE", "").strip() or get_settings().keycloak_client_id


def _brigade_enabled() -> bool:
    """Brigade verification is on only when a trusted issuer is configured."""
    return bool(_brigade_trusted_issuer())


def _brigade_trusted_issuer() -> str:
    return os.getenv("BRIGADE_TRUSTED_ISSUER", "").strip()


def _brigade_jwks_url() -> str:
    explicit = os.getenv("BRIGADE_JWKS_URL", "").strip()
    if explicit:
        return explicit
    issuer = _brigade_trusted_issuer().rstrip("/")
    return f"{issuer}/.well-known/jwks.json" if issuer else ""


def _brigade_expected_audience() -> str:
    return os.getenv("BRIGADE_EXPECTED_AUDIENCE", "brand-ops").strip()


# ---------------------------------------------------------------------------
# JWKS fetch + cache (one cache per issuer)
# ---------------------------------------------------------------------------

_JWKS_CACHE_TTL_SEC = 300
_kc_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_brigade_jwks_cache: dict[str, Any] = {"keys": None, "fetched_at": 0.0}

# A User-Agent header is required: Cloudflare's default WAF blocks the bare
# Python/httpx UA with error 1010 on the realm and Brigade hosts.
_UA = "brand-ops/0.1 (+https://brand-ops.magicunicorn.dev)"


async def _fetch_jwks(url: str, cache: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    now = time.time()
    if (
        not force
        and cache["keys"] is not None
        and (now - cache["fetched_at"]) < _JWKS_CACHE_TTL_SEC
    ):
        return cache["keys"]
    async with httpx.AsyncClient(timeout=5, headers={"User-Agent": _UA}) as client:
        response = await client.get(url)
        response.raise_for_status()
        keys = response.json()
    if not isinstance(keys, dict) or "keys" not in keys:
        raise ValueError("JWKS response missing 'keys' array")
    cache["keys"] = keys
    cache["fetched_at"] = now
    return keys


def _reset_caches_for_tests() -> None:
    """Test helper: clear both in-process JWKS caches."""
    for cache in (_kc_jwks_cache, _brigade_jwks_cache):
        cache["keys"] = None
        cache["fetched_at"] = 0.0


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        return None
    for k in keys:
        if isinstance(k, dict) and k.get("kid") == kid:
            return k
    return None


# ---------------------------------------------------------------------------
# Scope extraction (tolerant of OIDC singular vs Brigade plural)
# ---------------------------------------------------------------------------


def _extract_scopes(claims: dict[str, Any]) -> tuple[str, ...]:
    """Pull scopes from a verified-claims dict.

    Accepts EITHER ``scopes`` (a JSON array, Brigade's preferred plural) OR
    ``scope`` (a space-delimited string, the OIDC default). The plural wins when
    both are present. A malformed value degrades to an empty tuple rather than
    raising; downstream checks treat empty as "no scope granted", which is the
    safe, never-wider default.
    """
    plural = claims.get("scopes")
    if isinstance(plural, list):
        return tuple(str(item) for item in plural if item is not None)
    singular = claims.get("scope")
    if isinstance(singular, str) and singular:
        return tuple(singular.split())
    if isinstance(singular, list):
        return tuple(str(s) for s in singular if s is not None)
    return ()


# ---------------------------------------------------------------------------
# Keycloak verification
# ---------------------------------------------------------------------------


async def _verify_keycloak(token: str) -> Principal | None:
    """Verify a uchub Keycloak RS256 bearer. None on any failure.

    Signature, issuer, and expiry are enforced by jose. Audience is checked
    here against the configured client id, accepting either an ``aud`` member or
    a ``resource_access`` key (Keycloak stamps ``aud="account"`` widely, so a
    strict aud-only check would reject valid cross-client tokens).
    """
    if jwt is None:  # pragma: no cover - jose is a hard dependency
        return None
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except (JWTError, ValueError, KeyError):
        return None
    alg = header.get("alg")
    if alg not in _ACCEPTED_ALGS:
        return None
    kid = header.get("kid")
    if not kid:
        return None

    realm = str(settings.keycloak_realm_url).rstrip("/")
    certs_url = f"{realm}/protocol/openid-connect/certs"
    try:
        jwks = await _fetch_jwks(certs_url, _kc_jwks_cache)
        key = _find_key(jwks, kid)
        if key is None:
            jwks = await _fetch_jwks(certs_url, _kc_jwks_cache, force=True)
            key = _find_key(jwks, kid)
        if key is None:
            return None
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            issuer=realm,
            options={"verify_at_hash": False, "verify_aud": False},
        )
    except (JWTError, httpx.HTTPError, ValueError, KeyError) as exc:
        _log.info("keycloak token validation failed: %s", exc.__class__.__name__)
        return None

    expected_aud = _keycloak_audience()
    aud = claims.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud] if aud else []
    resource_access = claims.get("resource_access")
    in_resource_access = isinstance(resource_access, dict) and expected_aud in resource_access
    if expected_aud not in aud_list and not in_resource_access:
        _log.info("keycloak token rejected: audience does not include %r", expected_aud)
        return None

    uc_uid = claims.get("uc_uid") or claims.get("sub")
    if not isinstance(uc_uid, str) or not uc_uid:
        return None
    act = claims.get("act") or {}
    agent_id = act.get("sub") or act.get("client_id") if isinstance(act, dict) else None
    return Principal(
        uc_uid=uc_uid,
        agent_id=agent_id,
        scopes=_extract_scopes(claims),
        issuer="keycloak",
        claims=claims,
    )


# ---------------------------------------------------------------------------
# Brigade verification (separate issuer / audience / JWKS / signing scheme)
# ---------------------------------------------------------------------------


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _load_jwk_for_alg(jwk: dict[str, Any], alg: str):
    """Return a verifying key object for ``alg`` from a JWK dict."""
    kty = jwk.get("kty")
    if alg == "EdDSA":
        if kty != "OKP" or jwk.get("crv") != "Ed25519":
            raise ValueError("EdDSA requires kty=OKP and crv=Ed25519")
        x = jwk.get("x")
        if not isinstance(x, str):
            raise ValueError("EdDSA JWK missing 'x'")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        raw = _b64url_decode(x)
        if len(raw) != 32:
            raise ValueError("Ed25519 raw key must be 32 bytes")
        return Ed25519PublicKey.from_public_bytes(raw)
    if alg == "RS256":
        if kty != "RSA":
            raise ValueError("RS256 requires kty=RSA")
        from jose.backends.cryptography_backend import CryptographyRSAKey

        return CryptographyRSAKey(jwk, "RS256")
    raise ValueError(f"unsupported alg {alg!r}")


def _verify_brigade_signature(alg: str, key: Any, signing_input: bytes, signature: bytes) -> bool:
    if alg == "EdDSA":
        from cryptography.exceptions import InvalidSignature

        try:
            key.verify(signature, signing_input)
            return True
        except InvalidSignature:
            return False
        except Exception:  # pragma: no cover - defensive
            return False
    if alg == "RS256":
        try:
            return bool(key.verify(signing_input, signature))
        except Exception:  # pragma: no cover - defensive
            return False
    return False


async def _verify_brigade(token: str) -> Principal | None:
    """Verify a Brigade-minted JWT. None on any failure or when Brigade is off.

    Validates the strict alg allowlist, a kid present in Brigade's JWKS (with a
    one-shot rotation refresh), the signature, then the issuer, audience, and
    time-based claims. Only after the signature is good are claims trusted.
    """
    if not _brigade_enabled():
        return None
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict):
        return None
    alg = header.get("alg")
    if alg not in _ACCEPTED_ALGS:
        return None
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        return None

    jwks_url = _brigade_jwks_url()
    if not jwks_url:
        return None
    try:
        jwks = await _fetch_jwks(jwks_url, _brigade_jwks_cache)
        key_jwk = _find_key(jwks, kid)
        if key_jwk is None:
            jwks = await _fetch_jwks(jwks_url, _brigade_jwks_cache, force=True)
            key_jwk = _find_key(jwks, kid)
        if key_jwk is None:
            return None
        verifying_key = _load_jwk_for_alg(key_jwk, alg)
        signature = _b64url_decode(signature_b64)
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        _log.info("brigade token key/signature setup failed: %s", exc.__class__.__name__)
        return None

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if not _verify_brigade_signature(alg, verifying_key, signing_input, signature):
        _log.info("brigade token signature verification failed for kid=%r", kid)
        return None

    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None

    if claims.get("iss") != _brigade_trusted_issuer():
        return None
    expected_aud = _brigade_expected_audience()
    aud = claims.get("aud")
    aud_list = aud if isinstance(aud, list) else [aud] if aud else []
    if expected_aud not in aud_list:
        return None

    now = time.time()
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or now > float(exp) + _CLOCK_SKEW_SEC:
        return None
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and now + _CLOCK_SKEW_SEC < float(nbf):
        return None
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and now + _CLOCK_SKEW_SEC < float(iat):
        return None

    uc_uid = claims.get("uc_uid") or claims.get("sub")
    if not isinstance(uc_uid, str) or not uc_uid:
        return None
    act = claims.get("act") or {}
    agent_id = act.get("sub") or act.get("client_id") if isinstance(act, dict) else None
    return Principal(
        uc_uid=uc_uid,
        agent_id=agent_id,
        scopes=_extract_scopes(claims),
        issuer="brigade",
        claims=claims,
    )


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def authenticate(token: str) -> Principal | None:
    """Validate a raw bearer token. Keycloak first, then Brigade. None on miss."""
    principal = await _verify_keycloak(token)
    if principal is not None:
        return principal
    return await _verify_brigade(token)


async def require_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_workspace_id: Annotated[str | None, Header()] = None,
    ws: Annotated[str | None, Query()] = None,
) -> Principal:
    """FastAPI dependency yielding the validated, workspace-scoped caller.

    When AUTH_REQUIRED is false (the default) a missing or invalid token resolves
    to the anonymous principal with NO workspace scope, so local dev, the CLI,
    and tests see every brand (the original global view). When AUTH_REQUIRED is
    true a valid Keycloak or Brigade bearer is mandatory and anything else is
    401.

    For an authed caller the active workspace and the brands it may see are
    resolved from the caller's memberships (SUITE-IDENTITY D4/D5): the requested
    workspace (``X-Workspace-Id`` header, then the ``?ws=`` query fallback, then
    the token's ``workspace_id`` claim) when they are a member, else their
    default membership, else nothing. The ``?ws=`` fallback exists for ``<img>``
    GETs, which cannot send a custom header; like the header it is only a REQUEST
    that the resolver honors solely when the caller is a member, so it can never
    select a workspace the caller is not in. That scope is published to the
    ``brand_loader`` for the life of the request, so every brand read is confined
    to the active workspace -- and a caller with no membership sees no brands
    (deny-by-default), never the seed tenant's.
    """
    required = auth_required()
    if credentials is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return _anonymous_principal()

    principal = await authenticate(credentials.credentials)
    if principal is not None:
        return await _scope_principal(principal, x_workspace_id or ws)
    if required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Auth optional: a bad token does not block an open endpoint, but we do not
    # silently elevate it either; the caller is treated as anonymous.
    return _anonymous_principal()


async def _scope_principal(principal: Principal, x_workspace_id: str | None) -> Principal:
    """Resolve the caller's active workspace + readable brands, and publish them.

    Returns a principal carrying the active ``workspace_id`` and ``role``, and
    sets the request-scoped brand allow-list on the loader. A caller with no
    usable membership gets ``workspace_id=None`` and an empty allow-list, so they
    see no brands. The resolver fails closed (empty) on any database problem, so
    this never leaks another tenant's brands and never raises.
    """
    candidate = _candidate_workspace(principal.claims or {}, x_workspace_id)
    ctx = await resolve_context(principal.uc_uid, requested_workspace_id=candidate)
    set_workspace_scope(ctx.workspace_id, ctx.allowed_brand_slugs)
    # Publish the real caller for the activity feed: every event written during
    # this request records principal.uc_uid alongside its persona label.
    set_current_actor(principal.uc_uid)
    return replace(
        principal,
        workspace_id=ctx.workspace_id,
        workspace_slug=ctx.workspace_slug,
        role=ctx.role,
    )


def _anonymous_principal() -> Principal:
    """The anonymous caller: unscoped, so the CLI / local dev see every brand.

    Unscoped (allowed_brands None) is the original global view. AUTH_REQUIRED is
    true on every real deployment, so the anonymous principal only ever appears
    in local dev, the stdio MCP server, the CLI, and tests -- contexts where
    seeing all brands is intended. The scope is set as a side effect here so the
    open path behaves exactly like the authed path.
    """
    set_workspace_scope(None, None)
    # No authenticated caller: the activity feed records an unattributed
    # (null uc_uid) act, which reads as "system" -- the CLI / local / test path.
    set_current_actor(None)
    return Principal(uc_uid="anonymous", anonymous=True, issuer=None)


__all__ = (
    "ANONYMOUS",
    "Principal",
    "authenticate",
    "auth_required",
    "require_principal",
)
