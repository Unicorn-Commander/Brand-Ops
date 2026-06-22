"""Self-serve signup: first-login workspace provisioning for the authenticated caller.

The rest of the surface is deny-by-default: an authenticated suite user (a valid
uchub Keycloak or Brigade bearer) who has no ``workspace_member`` row resolves to
an empty context and sees no brands -- onboarding has always been out of band (an
admin grant or the boot allow-list). This module adds the missing self-serve path:
a caller with no workspace can mint their OWN personal workspace (owner, default)
and land in a working app, with no operator in the loop.

It is built to the same posture as the admin router:

  * OFF by default. ``BRAND_OPS_SELF_SERVE_SIGNUP`` (truthy) is the single switch;
    unset means the endpoints report "not enabled" and refuse to provision, so an
    existing deploy's access policy is unchanged until an operator opts in.
  * SELF ONLY. Provisioning always acts on ``principal.uc_uid`` (the validated
    token subject), never a body-supplied identity -- a caller can only ever
    create a workspace for themselves.
  * IDEMPOTENT. A caller who already has any active membership gets that workspace
    back (``created=false``), never a second one, so a retried or double-clicked
    signup cannot spam workspaces.
  * AUDITED. Each provision is recorded to a dedicated stdout logger and to the
    durable, tamper-evident admin-audit log (the same record an admin grant
    leaves), so a self-serve onboard is as traceable as an operator one.

Note on workspace id minting: elsewhere Brand-Ops only ever MIRRORS a registry
-minted workspace id (bootstrap, the admin create route take the id as input). A
self-serve personal workspace has no upstream registry call, so this is the one
place Brand-Ops mints its own id (uuid4). The id is a valid uuid the suite
registry can reconcile later; the workspace is Brand-Ops-local until it does.

Billing is deliberately not here. Brand-Ops has no native subscription/payment
system -- plan and spend caps live upstream (Lago / Ops-Center) and are governed
by env (``BRAND_OPS_IMAGE_CAP_CENTS``) -- so "signup" is provisioning only; a new
workspace is a free, ungated tenant exactly like every existing one.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .. import admin_audit
from ..db import repository as repo
from ..db.base import get_sessionmaker, scoped_session
from ..db.tenancy import resolve_context
from .auth import Principal, require_principal

_log = logging.getLogger("brand_ops.api.onboarding")
# A dedicated audit logger, mirroring the admin router: every provision emits one
# line here naming the caller (uc_uid) and the workspace they stood up.
_audit = logging.getLogger("brand_ops.onboarding.audit")

onboarding_router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

_Auth = Annotated[Principal, Depends(require_principal)]


# ---------------------------------------------------------------------------
# Feature switch (this module owns its own knob, like auth_required / admin_subs)
# ---------------------------------------------------------------------------


def self_serve_enabled() -> bool:
    """Whether a caller with no workspace may self-provision one. Default false.

    Read at call time from ``BRAND_OPS_SELF_SERVE_SIGNUP`` so a deploy can flip it
    without a code change and a test can set it with an env var. Off by default:
    an existing deployment's onboarding stays admin-only until an operator opts in.
    """
    return os.getenv("BRAND_OPS_SELF_SERVE_SIGNUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _require_db() -> None:
    """Raise a clean 503 when no database is configured for a provision write.

    Provisioning is meaningless without the tenancy DB, so rather than degrade
    silently it tells the caller the dependency is missing (mirrors the admin
    router's guard).
    """
    if not os.getenv("DATABASE_URL", "").strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database is not configured (DATABASE_URL unset)",
        )


# ---------------------------------------------------------------------------
# Name + slug derivation
# ---------------------------------------------------------------------------

# A workspace slug is globally unique (the workspace.slug constraint) and is the
# human label the switcher shows. Keep it to the same safe shape brand slugs use
# (lowercase alnum + hyphen, leading alnum), so it is always a clean single token.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SLUG_MAX = 60


def suggested_name(claims: dict[str, Any] | None, uc_uid: str) -> str:
    """A friendly default workspace name for a caller, from their token claims.

    Prefers the OIDC ``name``, then ``preferred_username``, then the local part of
    ``email``; falls back to a short, stable label off the subject so there is
    always something to pre-fill the signup field with. Never returns empty.
    """
    claims = claims or {}
    for key in ("name", "preferred_username"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    email = claims.get("email")
    if isinstance(email, str) and "@" in email:
        local = email.split("@", 1)[0].strip()
        if local:
            return local
    short = (uc_uid or "").strip()[:8]
    return f"workspace-{short}" if short else "My workspace"


def slugify(name: str | None) -> str:
    """Turn a free-text name into a safe workspace slug, or "" if nothing usable.

    Lowercases, replaces every run of non-alphanumeric characters with a single
    hyphen, trims hyphens, and caps the length. The result either matches
    ``_SLUG_RE`` or is empty (the caller substitutes a default), so a workspace
    slug is always a clean single token and never a path-unsafe value.
    """
    if not name:
        return ""
    lowered = name.strip().lower()
    hyphened = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    slug = hyphened[:_SLUG_MAX].strip("-")
    return slug if slug and _SLUG_RE.match(slug) else ""


async def _unique_slug(session, base: str) -> str:
    """Return ``base``, or the first free ``base-2``, ``base-3`` ... variant.

    The workspace slug is globally unique, so two callers with the same name would
    collide; this disambiguates with a readable numeric suffix. Falls back to a
    uuid-fragment suffix in the pathological case that dozens are already taken
    (and the unique constraint plus the caller's IntegrityError retry is the final
    backstop against the check-then-insert race).
    """
    if not await repo.workspace_slug_exists(session, base):
        return base
    for n in range(2, 50):
        candidate = f"{base}-{n}"
        if not await repo.workspace_slug_exists(session, candidate):
            return candidate
    return f"{base}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Provisioning service (HTTP-agnostic, unit-testable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvisionResult:
    """The outcome of a self-serve provision.

    ``created`` is False when the caller already had a workspace (the idempotent
    no-op path), True when a new one was stood up. ``role`` is the caller's role in
    the returned workspace (always ``owner`` for a freshly minted one).
    """

    workspace_id: str
    slug: str
    role: str
    created: bool


async def provision_personal_workspace(
    uc_uid: str,
    *,
    display_name: str | None = None,
    claims: dict[str, Any] | None = None,
) -> ProvisionResult:
    """Stand up (or return) the caller's own workspace. Self-only, idempotent.

    Idempotency first: if the caller already has an active membership, that
    workspace is returned unchanged (``created=False``) so a repeat call never
    mints a second one. Otherwise a new workspace anchor is created with a unique
    slug derived from ``display_name`` (or the caller's claims), and the caller is
    granted an ``owner``, default membership in it (user-scoped RLS, the same write
    the admin grant and the boot seeder use). The id is minted here (uuid4) since a
    personal workspace has no upstream registry id to mirror.

    Raises ``RuntimeError`` when no database is configured and ``SQLAlchemyError``
    on a database failure; the route maps those to 503 / 502.
    """
    # Idempotent: a caller who already has a workspace gets it back, never a
    # second. resolve_context fails closed (empty) on any DB problem, so a hiccup
    # here just means we proceed to create -- and the create surfaces the real
    # error rather than silently provisioning a duplicate on a transient read.
    existing = await resolve_context(uc_uid)
    if existing.workspace_id is not None:
        return ProvisionResult(
            workspace_id=existing.workspace_id,
            slug=existing.workspace_slug or "",
            role=existing.role or "owner",
            created=False,
        )

    base = slugify(display_name) or slugify(suggested_name(claims, uc_uid)) or "workspace"
    ws_id = str(uuid.uuid4())
    sm = get_sessionmaker()

    # Create the workspace anchor row. The slug is disambiguated against existing
    # workspaces first; the IntegrityError catch covers the rare check-then-insert
    # race (a concurrent signup taking the slug in between) by retrying once with a
    # uuid-fragment suffix that cannot collide.
    async with sm() as session:
        slug = await _unique_slug(session, base)
        try:
            async with session.begin():
                await repo.upsert_workspace(session, workspace_id=ws_id, slug=slug)
        except IntegrityError:
            slug = f"{base}-{uuid.uuid4().hex[:8]}"
            async with session.begin():
                await repo.upsert_workspace(session, workspace_id=ws_id, slug=slug)

    # Grant the caller an owner, default membership. User-scoped RLS: the
    # workspace_member policy keys on the member's own user_key, so this runs under
    # scoped_session(user_key=uc_uid) -- the same scope the admin grant and the
    # boot seeder use. A separate session keeps the membership write in its own
    # transaction, exactly like bootstrap.ensure_membership.
    async with sm() as session, scoped_session(session, user_key=uc_uid):
        await repo.upsert_membership(
            session,
            user_key=uc_uid,
            workspace_id=ws_id,
            role="owner",
            is_default=True,
        )

    _audit.info("self-serve provision: %s stood up workspace %s (slug=%s)", uc_uid, ws_id, slug)
    # Durable, tamper-evident record (root-level log: the new workspace's own feed
    # does not exist yet, and a "workspace created" event is inherently cross-tenant).
    admin_audit.append_admin_event(
        uc_uid, "self_provision_workspace", ws_id, {"slug": slug, "role": "owner"}
    )
    return ProvisionResult(workspace_id=ws_id, slug=slug, role="owner", created=True)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class OnboardingStatus(BaseModel):
    """What the GUI needs to decide whether to show the signup screen.

    ``enabled`` is the deploy switch. ``has_workspace`` is whether the caller
    already belongs to one. ``needs_onboarding`` is the single signal the gate
    reads: true only when self-serve is on AND the (non-anonymous) caller has no
    workspace, i.e. show the create-your-workspace screen. ``suggested_name``
    pre-fills the name field.
    """

    enabled: bool
    needs_onboarding: bool
    has_workspace: bool
    suggested_name: str | None = None


class ProvisionRequest(BaseModel):
    """Create the caller's own workspace. ``display_name`` is an optional label the
    caller picked; when blank the name is derived from their identity claims."""

    display_name: str | None = Field(default=None, max_length=120)


class ProvisionedWorkspace(BaseModel):
    workspace_id: str
    slug: str
    role: str
    is_default: bool = True
    created: bool


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@onboarding_router.get("", response_model=OnboardingStatus)
async def onboarding_status(principal: _Auth) -> OnboardingStatus:
    """Whether this caller should be offered self-serve signup.

    The anonymous principal (AUTH_REQUIRED off: local dev, the CLI) sees every
    brand and never needs onboarding. An authenticated caller needs onboarding
    only when self-serve is enabled and they have no active workspace
    (``workspace_id`` resolved to None, i.e. no membership). Pure read; provisions
    nothing.
    """
    enabled = self_serve_enabled()
    if principal.anonymous:
        return OnboardingStatus(
            enabled=enabled, needs_onboarding=False, has_workspace=False
        )
    has_workspace = principal.workspace_id is not None
    return OnboardingStatus(
        enabled=enabled,
        needs_onboarding=enabled and not has_workspace,
        has_workspace=has_workspace,
        suggested_name=suggested_name(principal.claims, principal.uc_uid),
    )


@onboarding_router.post("/provision", response_model=ProvisionedWorkspace)
async def provision(body: ProvisionRequest, principal: _Auth) -> ProvisionedWorkspace:
    """Provision (or return) the authenticated caller's own workspace.

    Refused with 403 when self-serve is disabled or the caller is anonymous (the
    unscoped local/CLI principal has no identity to provision for). Otherwise it
    mints the caller an owner, default workspace, or returns the one they already
    have. Always acts on ``principal.uc_uid`` -- the body never names another user.
    """
    if not self_serve_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="self-serve signup is not enabled",
        )
    if principal.anonymous:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="self-serve signup requires an authenticated user",
        )
    _require_db()
    try:
        result = await provision_personal_workspace(
            principal.uc_uid, display_name=body.display_name, claims=principal.claims
        )
    except RuntimeError as exc:
        _log.error("self-serve provision skipped: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database is not configured",
        ) from None
    except SQLAlchemyError as exc:
        _log.error("self-serve provision failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=502, detail="database error") from None
    return ProvisionedWorkspace(
        workspace_id=result.workspace_id,
        slug=result.slug,
        role=result.role,
        is_default=True,
        created=result.created,
    )


__all__ = (
    "onboarding_router",
    "self_serve_enabled",
    "suggested_name",
    "slugify",
    "provision_personal_workspace",
    "ProvisionResult",
)
