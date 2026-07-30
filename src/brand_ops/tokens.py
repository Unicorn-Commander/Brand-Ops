"""Personal access tokens: the durable Brand-Ops credential.

Every credential Brand-Ops accepted before this module was a short-lived JWT
minted by an external IdP (Keycloak uchub or Brigade). That is fine for a browser
and useless for a headless MCP client: the stdio MCP transport had nothing it
could hold, so it held nothing and ran with no identity at all. A PAT is the
missing primitive -- a long-lived bearer a machine can keep in its environment
and that Brand-Ops can verify, scope, expire, and revoke on its own.

Design rules, all enforced here:

  * **Prefix.** Every token starts with ``brand_pat_`` so it is recognisable in a
    log scrubber / secret scanner and can be routed to this verifier without ever
    being handed to the JWT path (and vice versa).
  * **Hash at rest.** Only ``sha256(raw_token)`` is stored. The plaintext is
    returned exactly once, at mint, and is never written to the database, a log
    line, or an error message. A lost token is revoked and re-minted.
  * **No self-carried authority.** A PAT names an OWNER (``user_key`` = uc_uid)
    and nothing else. Its workspace and its readable brands are re-resolved from
    ``workspace_member`` on every use, so removing the human's membership
    instantly removes the token's reach. The optional workspace / brand bindings
    only ever NARROW that, never widen it.
  * **Expiry + revocation, checked every time.** :func:`token_is_usable` is the
    single predicate; a row that is revoked, expired, or malformed is refused.
  * **A PAT cannot mint a PAT, and neither can a delegated agent.**
    :func:`assert_can_mint` refuses when the caller authenticated with a PAT, so
    a leaked token cannot be laundered into a fresh one that outlives its
    revocation -- and it refuses a DELEGATED credential (a Brigade/Keycloak
    act-token, ``agent_id`` set or an ``act`` claim present) for the same reason
    one step removed: that credential is deliberately short-lived and revocable
    by pulling the delegation, and a 365-day PAT minted from it would survive
    exactly that revocation while being attributed to a human who never took the
    action. Only a first-person, interactive SSO identity mints.
  * **A token is never wider than its minter was.** The minter's in-workspace
    ``role`` is recorded on the row and re-applied as a CAP on every use, so a
    token minted by a viewer stays a viewer's token even if its owner is later
    promoted. See :func:`cap_role`.
  * **Fail closed.** :func:`verify_token` returns ``None`` on *anything* that is
    not a positive match -- bad prefix, no row, revoked, expired, DB unset, DB
    unreachable, unexpected exception. There is no path where an error yields a
    principal.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

_log = logging.getLogger("brand_ops.tokens")

__all__ = [
    "PAT_PREFIX",
    "PatIdentity",
    "PatMintForbidden",
    "assert_can_mint",
    "cap_role",
    "display_prefix",
    "generate_token",
    "hash_token",
    "is_delegated",
    "looks_like_pat",
    "token_is_usable",
    "verify_token",
]

# The single recognisable marker. Also the reason a PAT is never handed to the
# JWT verifier: the dispatch in api.auth keys on exactly this.
PAT_PREFIX = "brand_pat_"

# 32 bytes of os.urandom -> 256 bits of entropy, url-safe base64 encoded. Far
# beyond guessable, and the hash lookup is a single indexed equality.
_SECRET_BYTES = 32

# How many characters of the secret are shown (with the prefix) in a listing so a
# human can tell two tokens apart. Non-secret by construction: 8 base64 chars of
# a 256-bit secret leaves ~208 bits unknown.
_DISPLAY_CHARS = 8


class PatMintForbidden(PermissionError):
    """Raised when a credential that may not mint a PAT tries to mint one."""


@dataclass(frozen=True)
class PatIdentity:
    """A verified PAT, reduced to what the auth layer needs.

    Carries no grants: ``user_key`` is the identity the caller acts as, and the
    optional bindings are narrowing constraints the scoper applies on top of the
    owner's real memberships.

    ``role`` is the minter's in-workspace role AT MINT TIME. It is likewise never
    a grant -- :func:`cap_role` intersects it with the owner's live role, so it
    can only ever hold the token BELOW what its owner can do today.

    There is deliberately no ``scopes`` field. The column existed and was carried
    all the way onto the ``Principal``, and then nothing anywhere consulted it:
    a scope string on a token neither widened nor narrowed a single decision. A
    security control that does nothing is worse than an absent one, because a
    client sets it and believes it. It is replaced by ``role``, which IS enforced
    on every call. If a real capability vocabulary is ever defined (one string per
    gated operation, checked at the same chokepoint as the role), it comes back
    then -- with its enforcement, not before it.
    """

    token_id: str
    user_key: str
    role: str | None = None
    workspace_id: str | None = None
    brand_slug: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers (no DB, no network -- unit-testable on their own)
# ---------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    """``sha256(raw)`` as lowercase hex -- the only form ever persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def display_prefix(raw: str) -> str:
    """The non-secret display fragment for a listing (``brand_pat_ab12cd34``)."""
    return raw[: len(PAT_PREFIX) + _DISPLAY_CHARS]


def generate_token() -> tuple[str, str, str]:
    """Mint a fresh token. Returns ``(raw, token_hash, token_prefix)``.

    ``raw`` is the ONLY time the plaintext exists in this process; the caller
    hands it straight to the requester and persists the other two.
    """
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(_SECRET_BYTES)}"
    return raw, hash_token(raw), display_prefix(raw)


def looks_like_pat(token: object) -> bool:
    """Whether ``token`` is shaped like one of our PATs.

    Used only to ROUTE a credential to the right verifier -- it proves nothing
    about validity. The length floor keeps a bare prefix (``"brand_pat_"``) from
    reaching the database at all.
    """
    return (
        isinstance(token, str)
        and token.startswith(PAT_PREFIX)
        and len(token) >= len(PAT_PREFIX) + 16
    )


def token_is_usable(
    *,
    revoked_at: datetime | None,
    expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Whether a stored token row may still authenticate. Fail closed.

    Revoked is permanent. An expiry in the past is refused. A naive timestamp
    (a database configured without timezone awareness) is read as UTC rather
    than raising, because raising here would be an error path -- and every error
    path in this module must DENY, not crash into an ambiguous outcome.
    """
    if revoked_at is not None:
        return False
    if expires_at is None:
        return True
    moment = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return expires_at > moment


def assert_can_mint(
    credential_kind: str | None,
    *,
    anonymous: bool = False,
    delegated: bool = False,
) -> None:
    """Who may mint a durable credential. Every branch here DENIES.

    A durable credential must be traceable to an interactive, first-person human
    decision. Three callers are refused:

      * the anonymous / unauthenticated principal -- it is nobody, so the token
        would belong to nobody;
      * a caller who themselves authenticated with a PAT. If a PAT could mint a
        PAT, a leaked token could roll itself forward forever and revoking the
        original would not stop it -- the revocation would apply to a credential
        the attacker no longer needs;
      * a DELEGATED agent credential. This is the same laundering one step
        removed, and it was the live hole: ``credential_kind == "brigade"`` was
        simply allowed, and nothing looked at ``agent_id`` or the ``act`` claim.
        A Brigade act-token is minted short-lived precisely so that pulling the
        delegation ends the agent's reach; a 365-day PAT minted from it survives
        that revocation, is indistinguishable from one the human made, and is
        attributed to the human in the audit line. Refusing is the honest
        posture: an agent that needs durable reach gets a PAT its principal minted
        for it, deliberately, in a session the human was actually present for.

    A first-person Keycloak or Brigade identity (no ``act``, no ``agent_id``) is
    the only thing that mints.
    """
    if anonymous or credential_kind is None:
        raise PatMintForbidden(
            "minting a personal access token requires an authenticated suite identity"
        )
    if credential_kind == "pat":
        raise PatMintForbidden(
            "a personal access token cannot mint another personal access token; "
            "sign in with your suite identity to issue a new one"
        )
    if delegated:
        raise PatMintForbidden(
            "a delegated agent credential cannot mint a personal access token; "
            "the delegation is revocable and a durable token would outlive it. "
            "Ask the person you act for to issue one from their own session."
        )


def is_delegated(principal: object) -> bool:
    """Whether a principal is acting under a DELEGATION rather than in person.

    True when the credential names an agent -- ``agent_id`` (which ``api.auth``
    fills from the token's ``act.sub`` / ``act.client_id``) or a bare ``act``
    claim that the verifier did not manage to reduce to an id. Reading the raw
    claim as well as the derived field is the fail-closed choice: a differently
    shaped ``act`` must still count as delegation, not fall through as first
    person.
    """
    if getattr(principal, "agent_id", None):
        return True
    claims = getattr(principal, "claims", None) or {}
    return bool(isinstance(claims, dict) and claims.get("act"))


# The role hierarchy, duplicated here from ``api.auth`` on purpose: this module
# is the credential layer and must not import the HTTP layer (``api.auth``
# imports THIS module). ``api.auth._ROLE_RANK`` is asserted equal to it by test,
# so the two can never silently diverge.
_ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def cap_role(live_role: str | None, minted_role: str | None) -> str | None:
    """The effective role of a PAT session: the LOWER of live and mint-time.

    ``live_role`` is what the owner's membership says today; ``minted_role`` is
    what it said when the token was issued. Returning the lower of the two makes
    the token a narrowing of its minter in the time dimension as well as the
    workspace and brand ones: a token minted by a viewer never becomes an owner's
    token because its owner was later promoted, and a token minted by an owner
    who was later demoted follows them down.

    An unrecognised or missing ``minted_role`` caps at the floor (``viewer``)
    rather than being ignored -- indeterminate must deny.
    """
    if live_role is None:
        return None
    minted = str(minted_role or "").lower()
    if minted not in _ROLE_RANK:
        minted = "viewer"
    if _ROLE_RANK[minted] < _ROLE_RANK.get(str(live_role).lower(), 0):
        return minted
    return live_role


# ---------------------------------------------------------------------------
# Verification (the one DB read on the hot path)
# ---------------------------------------------------------------------------


async def verify_token(raw: str) -> PatIdentity | None:
    """Verify a presented PAT. ``None`` for anything that is not a live match.

    FAIL CLOSED, without exception: a bad prefix, an unknown hash, a revoked or
    expired row, an unset ``DATABASE_URL``, an unreachable database, or any
    unexpected error all return ``None``. Nothing here ever logs, echoes, or
    returns the presented secret -- only its row id and its display prefix.
    """
    if not looks_like_pat(raw):
        return None
    digest = hash_token(raw)
    try:
        from sqlalchemy import select

        from .db.base import get_sessionmaker
        from .db.models import ApiToken

        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ApiToken).where(ApiToken.token_hash == digest)
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None
                if not token_is_usable(
                    revoked_at=row.revoked_at, expires_at=row.expires_at
                ):
                    _log.info(
                        "api token refused (revoked or expired): id=%s prefix=%s",
                        row.id,
                        row.token_prefix,
                    )
                    return None
                return PatIdentity(
                    token_id=str(row.id),
                    user_key=row.user_key,
                    role=row.role,
                    workspace_id=str(row.workspace_id) if row.workspace_id else None,
                    brand_slug=row.brand_slug,
                )
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: any error denies
        _log.error("api token verification failed: %s", exc.__class__.__name__)
        return None
