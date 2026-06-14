"""Request-scoped tenancy resolution (SUITE-IDENTITY D4/D5).

The single place that turns a verified caller (a uchub ``sub``) plus an optional
requested workspace into the workspace this request acts in and the brands it
may see. The active workspace is chosen by precedence:

  1. an explicit requested workspace (the ``X-Workspace-Id`` header or the
     token's ``workspace_id`` claim) -- but ONLY when the caller is an active
     member of it; a request for a workspace they are not in is denied, never
     silently swapped for another;
  2. otherwise the caller's default membership (``is_default``, else the first
     active one).

A caller with no usable membership gets an empty context: no active workspace,
no brands -- deny-by-default. No token ever auto-provisions a membership;
memberships are seeded out of band (allow-list / registry mirror) and gate
access. A database that is unset or unreachable also yields an empty context
(logged), so a misconfiguration or outage fails closed, never open and never as
a 500.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from . import repository as repo
from .base import get_sessionmaker, scoped_session

_log = logging.getLogger("brand_ops.db.tenancy")


@dataclass(frozen=True)
class Membership:
    """One active membership, as the switcher and the resolver see it."""

    workspace_id: str
    slug: str
    role: str
    is_default: bool


@dataclass(frozen=True)
class WorkspaceContext:
    """The resolved tenancy of one request.

    ``workspace_id`` is the active workspace, or None when the caller has no
    usable membership (deny-by-default). ``role`` is the caller's role in it.
    ``allowed_brand_slugs`` is the set of brand slugs that workspace owns, which
    the file loader filters reads against. ``memberships`` is every active
    membership, for the workspace switcher.
    """

    workspace_id: str | None = None
    workspace_slug: str | None = None
    role: str | None = None
    allowed_brand_slugs: frozenset[str] = frozenset()
    memberships: tuple[Membership, ...] = ()


def coerce_uuid(value: str | None) -> str | None:
    """Return the canonical str form of a uuid, or None if it does not parse.

    A requested workspace id from a header or a token claim is untrusted text;
    parsing it as a uuid here means a malformed value is ignored (treated as no
    request) and a valid one is normalised before it is ever bound into a query
    or an RLS setting.
    """
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _pick_active(
    memberships: tuple[Membership, ...], requested: str | None
) -> Membership | None:
    """Apply the D4 precedence to choose the active membership, or None.

    An explicit request (header / claim) wins ONLY when the caller is an active
    member of it; otherwise -- a stale switcher selection, a tampered header, or
    no request -- it falls back to the caller's default membership (then the
    first active one). The caller therefore only ever lands in a workspace they
    belong to: the worst case for a bad request is seeing their own default, not
    a workspace they are not a member of, and not an empty lockout. With no
    memberships at all the result is None (deny-by-default).
    """
    by_id = {m.workspace_id: m for m in memberships}
    if requested is not None and requested in by_id:
        return by_id[requested]
    return next((m for m in memberships if m.is_default), None) or (
        memberships[0] if memberships else None
    )


async def resolve_context(
    uc_uid: str, *, requested_workspace_id: str | None = None
) -> WorkspaceContext:
    """Resolve the active workspace + readable brands for a verified caller.

    Reads the caller's memberships (user-scoped RLS), picks the active workspace
    by precedence, then loads that workspace's brand slugs (workspace-scoped
    RLS). Any database problem -- not configured, unreachable -- is caught and
    degrades to an empty context, so the caller simply sees nothing rather than
    another tenant's data or a 500.
    """
    requested = coerce_uuid(requested_workspace_id)
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            async with scoped_session(session, user_key=uc_uid):
                rows = await repo.list_user_memberships(session, uc_uid)
            memberships = tuple(
                Membership(
                    workspace_id=str(r.workspace_id),
                    slug=r.slug,
                    role=r.role,
                    is_default=r.is_default,
                )
                for r in rows
            )
            active = _pick_active(memberships, requested)
            if active is None:
                return WorkspaceContext(memberships=memberships)
            async with scoped_session(session, workspace_id=active.workspace_id):
                slugs = await repo.list_brand_slugs(session, active.workspace_id)
            return WorkspaceContext(
                workspace_id=active.workspace_id,
                workspace_slug=active.slug,
                role=active.role,
                allowed_brand_slugs=slugs,
                memberships=memberships,
            )
    except RuntimeError as exc:
        # get_engine raises RuntimeError when DATABASE_URL is unset. Authed but
        # no DB configured is a deploy error; fail closed and say so loudly.
        _log.error("tenancy resolution skipped: %s", exc)
        return WorkspaceContext()
    except (SQLAlchemyError, OSError) as exc:
        # A DB error, or a raw connect-phase failure (refused / DNS / timeout --
        # all OSError subclasses) degrades to deny, never a 500 or a leak.
        _log.error("tenancy resolution failed: %s", exc.__class__.__name__)
        return WorkspaceContext()
    except Exception as exc:  # noqa: BLE001 - fail CLOSED: any error denies, never leaks
        _log.error("tenancy resolution failed (unexpected): %s", exc.__class__.__name__)
        return WorkspaceContext()


async def record_brand(
    workspace_id: str,
    *,
    slug: str,
    display_name: str,
    parent_slug: str | None = None,
    tagline: str | None = None,
    created_by: str | None = None,
) -> None:
    """Index a just-created brand into the DB under its workspace.

    The brand-create surfaces (REST intake/clone, the equivalent MCP tools) call
    this right after the file is written, so the brand joins its workspace's
    allow-list and is visible on the next request -- not only after the next boot
    import. The file write already succeeded, so a DB error here only delays the
    brand's appearance (the boot import will still pick it up); the caller logs
    it rather than failing the create.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        async with scoped_session(session, workspace_id=workspace_id):
            await repo.upsert_brand(
                session,
                workspace_id,
                slug=slug,
                display_name=display_name,
                parent_slug=parent_slug,
                tagline=tagline,
                status="active",
                created_by=created_by,
            )
