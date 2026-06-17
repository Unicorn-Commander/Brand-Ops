"""Load and validate brand.yaml, with sub-brand inheritance.

A brand with `parent: <slug>` inherits the parent's visual and voice defaults
and its prompts, then overlays its own. The database import (a later
workstream) and the future service backend both call load_brand, so there is
one code path from file or DB to a validated Brand.
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import Path

import yaml

from .models import Brand

DEFAULT_KITS_DIR = Path(__file__).resolve().parents[2] / "brand-kits"

# ---------------------------------------------------------------------------
# Request-scoped tenant (workspace) scoping
# ---------------------------------------------------------------------------
#
# Scoping is centralized here, at the single read boundary every surface flows
# through, so the REST routes do not each have to thread a workspace through.
# ``require_principal`` resolves the caller's active workspace and the set of
# brand slugs it owns (from the DB, under row level security) and sets that
# here; ``list_brands`` and ``load_brand`` then reveal only those brands.
#
# The scope is a (workspace_id, allowed_brands) pair:
#   * ``allowed_brands is None``       -> UNSCOPED: the original global view, for
#     the CLI, the stdio MCP server, and any test or local run that never sets a
#     scope (AUTH_REQUIRED off). Backward compatible.
#   * ``allowed_brands == frozenset()`` -> DENY: an authed caller with no usable
#     membership sees nothing.
#   * ``allowed_brands == frozenset({...})`` -> only those slugs are visible.
# ``workspace_id`` is the active workspace, carried for the write path to stamp
# new brands into the DB. A contextvar (not a module global) keeps the scope
# per-request and prevents bleed between concurrent async requests.
_scope: ContextVar[tuple[str | None, frozenset[str] | None]] = ContextVar(
    "brand_ops_scope", default=(None, None)
)


def set_workspace_scope(
    workspace_id: str | None, allowed_brands: frozenset[str] | None
):
    """Set the request-scoped (workspace_id, allowed_brands). Returns the Token.

    ``require_principal`` calls this with the resolved workspace and its brand
    slug set so every read inside the request is scoped to that tenant. Passing
    ``allowed_brands=None`` (or never calling this) disables scoping = the
    original global behavior.
    """
    return _scope.set((workspace_id, allowed_brands))


def get_current_workspace_id() -> str | None:
    """The active workspace id for this request, or None when unscoped/denied."""
    return _scope.get()[0]


def get_allowed_brands() -> frozenset[str] | None:
    """The brand slugs visible to this request: None = all (unscoped), else the set."""
    return _scope.get()[1]


def workspace_query_suffix() -> str:
    """``&ws=<id>`` for the active workspace, or ``''`` when unscoped.

    Asset-file URLs carry this so an ``<img>`` GET -- which cannot send the
    ``X-Workspace-Id`` header the GUI's fetch() calls use -- still names the
    workspace it belongs to. ``require_principal`` accepts ``?ws=`` as a
    membership-gated fallback, so a multi-workspace caller's images resolve
    against the workspace they are viewing, not just their default (the bug a
    single-tenant deploy never hits, but a public multi-tenant one does). The
    workspace id is an opaque uuid, never a secret. Empty when unscoped (CLI /
    stdio MCP / tests), where every brand is already visible anyway.
    """
    ws = get_current_workspace_id()
    return f"&ws={ws}" if ws else ""


def reset_workspace_scope(token=None) -> None:
    """Clear the request-scoped workspace scope.

    With a Token (from :func:`set_workspace_scope`) it restores the prior value;
    without one it hard-resets to unscoped. Tests use the no-arg form so scope
    never leaks between tests.
    """
    if token is not None:
        _scope.reset(token)
    else:
        _scope.set((None, None))


def allow_brand(slug: str) -> None:
    """Add a freshly created brand to the current request's allow-list.

    A scoped create (intake / clone) writes a new brand whose slug is not in the
    allow-list the resolver loaded at the start of the request; without this the
    create path's own read-back of the just-written brand would 404. No-op when
    unscoped (allowed is None), where every brand is already visible. The DB row
    the route records makes the brand visible on subsequent requests too.
    """
    workspace_id, allowed = _scope.get()
    if allowed is not None and slug not in allowed:
        _scope.set((workspace_id, allowed | {slug}))

# A brand slug is one path segment of lowercase letters, digits, and hyphens —
# the exact shape brand_clone enforces on a write. Validating it here, at the
# single read boundary every surface flows through (the REST routes, the MCP
# tools, the compliance gate, the parent-merge recursion), is the one place that
# closes path traversal for all of them: a slug like ``../etc`` or an absolute
# path can never be joined onto the kits root to read a brand.yaml outside the
# tree. Defense at the boundary, not at each of a dozen callers.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class BrandNotFound(Exception):
    pass


def _validate_slug(slug: str) -> None:
    """Reject any slug that is not a safe single path segment.

    Raises ``BrandNotFound`` (not ValueError) so an unknown-shaped slug reads as
    "no such brand" to a caller, the same 404 a genuinely missing brand yields,
    rather than leaking that the input was treated as a path. A slug carrying a
    path separator, a ``..``, an absolute marker, or a NUL is never joined onto
    the kits root.
    """
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise BrandNotFound(f"brand {slug!r} not found")


def list_brands(kits_dir: Path | None = None) -> list[str]:
    root = kits_dir or DEFAULT_KITS_DIR
    if not root.exists():
        return []
    slugs = sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "brand.yaml").exists()
    )
    allowed = get_allowed_brands()
    if allowed is None:
        # Unscoped (CLI / stdio MCP / tests): the original global listing.
        return slugs
    # Tenant-scoped: only the brands this workspace owns, per the DB allow-list
    # the resolver loaded. A simple set membership test -- no per-brand load.
    return [s for s in slugs if s in allowed]


def _read_yaml(slug: str, kits_dir: Path) -> dict:
    # Validate before the path is built: this is the chokepoint both load_brand
    # and the parent-merge recursion call, so confining the slug here confines
    # every brand read in the package against traversal.
    _validate_slug(slug)
    path = kits_dir / slug / "brand.yaml"
    if not path.exists():
        available = ", ".join(list_brands(kits_dir)) or "(none)"
        raise BrandNotFound(f"brand {slug!r} not found. Available: {available}")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _merge(parent: dict, child: dict) -> dict:
    """Overlay child over parent. Visual and voice merge section by section so a
    sub-brand can override just a few colors; everything else replaces wholesale.
    """
    merged = dict(parent)
    for key, value in child.items():
        mergeable = key in ("visual", "voice")
        if mergeable and isinstance(value, dict) and isinstance(merged.get(key), dict):
            section = dict(merged[key])
            section.update(value)
            merged[key] = section
        else:
            merged[key] = value
    return merged


def load_brand(slug: str, kits_dir: Path | None = None) -> Brand:
    # Tenant guard first: a brand outside the request's workspace allow-list
    # reads as "no such brand" (404, never a 403 leak), and we never even parse
    # another tenant's file. Unscoped (allowed is None: CLI / stdio MCP / tests)
    # this never fires.
    allowed = get_allowed_brands()
    if allowed is not None and slug not in allowed:
        raise BrandNotFound(f"brand {slug!r} not found")
    root = kits_dir or DEFAULT_KITS_DIR
    raw = _read_yaml(slug, root)
    parent_slug = (raw.get("brand") or {}).get("parent")
    if parent_slug:
        parent_raw = _read_yaml(parent_slug, root)
        raw = _merge(parent_raw, raw)
    return Brand.model_validate(raw)


def resolve_reference_images(
    slug: str, brand: Brand, kits_dir: Path | None = None
) -> tuple[dict[str, Path], list[str]]:
    """Return (existing refs by role, missing role names).

    A missing reference is a warning, not an error: composition mode is optional.
    """
    root = kits_dir or DEFAULT_KITS_DIR
    base = root / slug
    found: dict[str, Path] = {}
    missing: list[str] = []
    for role, ref in brand.reference_images.items():
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = (base / ref).resolve()
        if candidate.exists():
            found[role] = candidate
        else:
            missing.append(role)
    return found, missing
