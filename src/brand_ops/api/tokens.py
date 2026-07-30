"""Personal access token management: mint, list, revoke.

The credential itself lives in :mod:`brand_ops.tokens`; this is the human door to
it. A PAT is what makes the MCP surfaces installable by a headless client -- an
agent config, a CI job, or the stdio transport's ``BRAND_OPS_MCP_TOKEN`` -- now
that neither surface will serve an unidentified caller.

Posture, all enforced below and covered by tests:

  * **Only a first-person SSO identity mints.** ``tokens.assert_can_mint``
    refuses the anonymous principal (the AUTH_REQUIRED-off local path), a caller
    who themselves authenticated with a PAT, and a DELEGATED agent credential (an
    ``act``/``agent_id`` token). A leaked or delegated credential therefore cannot
    roll itself forward past its own revocation.
  * **Role-gated.** Minting needs at least ``_MIN_MINT_ROLE`` in the active
    workspace. A viewer is a read-only member and had no way to write anything
    through the product; before this gate they could mint a 365-day headless
    credential, which is an operational capability their role does not carry.
  * **Self only.** The token is always minted for ``principal.uc_uid``. There is
    no body field for an owner.
  * **Never wider than the minter.** The workspace binding must be the caller's
    own resolved active workspace, a brand binding must be a brand that workspace
    can already see, and the minter's role at mint time is recorded and re-applied
    as a CAP on every use. A PAT is a narrowing of its owner, never a grant --
    including later, if the owner is promoted.
  * **Shown once.** The plaintext is in the 201 response body and nowhere else --
    not the database, not a log line, not a later GET.
  * **Revocable.** ``DELETE`` stamps ``revoked_at``; the verifier checks it on
    every use, so revocation is immediate rather than waiting for expiry.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .. import tokens as pat_tokens
from ..brand_loader import get_allowed_brands
from ..db.base import get_sessionmaker
from ..db.models import ApiToken
from .auth import _ROLE_RANK, Principal, require_principal

_log = logging.getLogger("brand_ops.api.tokens")
# A dedicated audit logger, mirroring the admin and onboarding routers: every
# mint and every revoke leaves a line naming the caller and the token ROW (never
# the secret).
_audit = logging.getLogger("brand_ops.tokens.audit")

tokens_router = APIRouter(prefix="/api/tokens", tags=["tokens"])

_Auth = Annotated[Principal, Depends(require_principal)]

# A PAT is a long-lived credential, so it gets a bounded default life rather than
# an unbounded one. 365 days is the ceiling; "no expiry" is not offered.
_DEFAULT_EXPIRY_DAYS = 90
_MAX_EXPIRY_DAYS = 365

# The floor role that may issue a durable headless credential. A ``viewer`` is a
# read-only member: they can write nothing through the REST surface and, since
# the MCP guard now mirrors those gates, nothing through the tool surface either.
# Issuing a 365-day credential that a machine holds is an operational capability,
# so it starts at ``member``. Fail-closed by construction: an unknown or missing
# role ranks 0 (below ``member``) and is refused.
_MIN_MINT_ROLE = "member"


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120, description="Human label for this token.")
    workspace_id: str | None = Field(
        default=None,
        description=(
            "Pin the token to one workspace. Must be the caller's own active "
            "workspace; omit to pin it to that workspace anyway."
        ),
    )
    brand_slug: str | None = Field(
        default=None,
        description="Optionally narrow the token to a single brand the caller can see.",
    )
    expires_in_days: int = Field(
        default=_DEFAULT_EXPIRY_DAYS, ge=1, le=_MAX_EXPIRY_DAYS
    )


class TokenOut(BaseModel):
    id: str
    name: str
    token_prefix: str
    # The minter's role at mint time: the cap this token can never exceed.
    # Server-assigned, never accepted from the request body.
    role: str | None
    workspace_id: str | None
    brand_slug: str | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TokenCreated(TokenOut):
    token: str = Field(description="The plaintext token. Shown once; store it now.")


def _require_db() -> None:
    """Clean 503 when no database is configured, rather than a 500 deeper down."""
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database is not configured (DATABASE_URL unset)",
        )


def _row_out(row: ApiToken) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "token_prefix": row.token_prefix,
        "role": row.role,
        "workspace_id": str(row.workspace_id) if row.workspace_id else None,
        "brand_slug": row.brand_slug,
        "expires_at": row.expires_at,
        "revoked_at": row.revoked_at,
        "created_at": row.created_at,
    }


def check_mint_allowed(principal: Principal) -> None:
    """Translate the credential-kind policy into an HTTP 403. Fail closed.

    Covers all three refusals: anonymous, a PAT minting a PAT, and a DELEGATED
    agent credential (``act`` / ``agent_id``) minting a durable one. The last was
    the open hole -- ``credential_kind == "brigade"`` was allowed outright and
    nothing here or in ``tokens`` ever looked at whether the Brigade token was a
    person or an agent acting for one.
    """
    try:
        pat_tokens.assert_can_mint(
            principal.credential_kind,
            anonymous=principal.anonymous,
            delegated=pat_tokens.is_delegated(principal),
        )
    except pat_tokens.PatMintForbidden as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


def check_mint_role(principal: Principal) -> None:
    """Require at least ``_MIN_MINT_ROLE`` in the active workspace. Fail closed.

    Uses the same ``_ROLE_RANK`` hierarchy and the same unknown-role-ranks-zero
    rule as ``auth.require_role``, so the token door cannot rank a role
    differently from every other gated route.
    """
    if _ROLE_RANK.get((principal.role or "").lower(), 0) < _ROLE_RANK[_MIN_MINT_ROLE]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"issuing a personal access token requires at least the "
                f"{_MIN_MINT_ROLE} role in this workspace"
            ),
        )


@tokens_router.post("", response_model=TokenCreated, status_code=201)
async def create_token(body: TokenCreate, principal: _Auth) -> dict[str, Any]:
    """Mint a personal access token for the calling identity. Shown once."""
    # Authorization first, availability second: an unauthorized caller must get
    # the same refusal whether or not the database happens to be reachable, and
    # must not learn anything about the node from trying.
    check_mint_allowed(principal)
    check_mint_role(principal)
    _require_db()

    active_ws = principal.workspace_id
    if not active_ws:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you have no active workspace to issue a token for",
        )
    if body.workspace_id and body.workspace_id != active_ws:
        # The caller may only bind a token to the workspace this request already
        # resolved them into (switch with X-Workspace-Id and mint again). This is
        # the membership gate: the resolver would never have produced a workspace
        # they are not an active member of.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a token can only be bound to your active workspace",
        )
    if body.brand_slug:
        allowed = get_allowed_brands()
        if allowed is not None and body.brand_slug not in allowed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"brand {body.brand_slug!r} not found",
            )

    raw, digest, prefix = pat_tokens.generate_token()
    expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)
    row = ApiToken(
        user_key=principal.uc_uid,
        name=body.name.strip(),
        token_hash=digest,
        token_prefix=prefix,
        # The cap. Taken from the RESOLVED principal (the caller's live
        # membership role), never from the request body.
        role=(principal.role or "").lower() or None,
        workspace_id=uuid.UUID(active_ws),
        brand_slug=body.brand_slug,
        expires_at=expires_at,
        created_by=principal.uc_uid,
    )
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                session.add(row)
            await session.refresh(row)
    except (SQLAlchemyError, OSError, RuntimeError) as exc:
        _log.error("api token mint failed: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="could not issue a token right now",
        ) from exc

    # The secret is deliberately absent from this line: prefix + row id only.
    _audit.info(
        "api_token.mint user=%s token=%s prefix=%s workspace=%s brand=%s role=%s",
        principal.uc_uid,
        row.id,
        prefix,
        active_ws,
        body.brand_slug,
        row.role,
    )
    out = _row_out(row)
    out["token"] = raw
    return out


@tokens_router.get("", response_model=list[TokenOut])
async def list_tokens(principal: _Auth) -> list[dict[str, Any]]:
    """Every token this identity owns. Never returns a secret or its hash."""
    if principal.anonymous:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="listing tokens requires an authenticated suite identity",
        )
    _require_db()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        select(ApiToken)
                        .where(ApiToken.user_key == principal.uc_uid)
                        .order_by(ApiToken.created_at.desc())
                    )
                ).scalars().all()
                return [_row_out(r) for r in rows]
    except (SQLAlchemyError, OSError, RuntimeError) as exc:
        _log.error("api token listing failed: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="could not list tokens right now",
        ) from exc


@tokens_router.delete("/{token_id}", status_code=204)
async def revoke_token(token_id: str, principal: _Auth) -> None:
    """Revoke one of the caller's own tokens. Immediate, and irreversible."""
    if principal.anonymous:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="revoking a token requires an authenticated suite identity",
        )
    _require_db()
    try:
        row_id = uuid.UUID(token_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="token not found"
        ) from exc
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ApiToken).where(
                            ApiToken.id == row_id,
                            # Ownership is part of the lookup, so another user's
                            # token id reads as "not found", never as a 403 that
                            # confirms the id exists.
                            ApiToken.user_key == principal.uc_uid,
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND, detail="token not found"
                    )
                if row.revoked_at is None:
                    row.revoked_at = datetime.now(UTC)
    except HTTPException:
        raise
    except (SQLAlchemyError, OSError, RuntimeError) as exc:
        _log.error("api token revoke failed: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="could not revoke the token right now",
        ) from exc
    _audit.info("api_token.revoke user=%s token=%s", principal.uc_uid, token_id)
