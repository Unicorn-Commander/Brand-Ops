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
# Request-scoped tenant (org) scoping
# ---------------------------------------------------------------------------
#
# Org scoping is centralized here, at the single read boundary every surface
# flows through, so the REST routes do not each have to thread an org through.
# ``require_principal`` sets ``current_org`` to the caller's org for the life of
# a request; ``list_brands`` and ``load_brand`` then filter against it.
#
# The default is ``None``, which means "no scoping": the CLI, the MCP server,
# and any test that never sets it keep the original global view, so this is
# fully backward compatible. A contextvar (not a module global) is used so the
# scope is per-request and never bleeds between concurrent async requests.
_current_org: ContextVar[str | None] = ContextVar("brand_ops_current_org", default=None)


def set_current_org(org: str | None):
    """Set the request-scoped org. Returns the contextvar Token for reset.

    ``require_principal`` calls this with the resolved principal's org so every
    brand read inside the request is scoped to that tenant. Passing ``None``
    (or never calling this) disables scoping = the original global behavior.
    """
    return _current_org.set(org)


def get_current_org() -> str | None:
    """The org scoping the current request, or ``None`` when unscoped."""
    return _current_org.get()


def reset_current_org(token=None) -> None:
    """Clear the request-scoped org.

    With a Token (from :func:`set_current_org`) it restores the prior value;
    without one it hard-resets to ``None``. Tests use the no-arg form in a
    fixture so org state never leaks between tests.
    """
    if token is not None:
        _current_org.reset(token)
    else:
        _current_org.set(None)

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
    org = get_current_org()
    if org is None:
        # Unscoped (CLI / MCP / tests): the original global listing.
        return slugs
    # Tenant-scoped: keep only brands owned by the current org. Loading each
    # brand to read its meta.org is the price of file-based storage; a brand
    # that fails to load is skipped rather than crashing the list, matching how
    # the REST layer already tolerates an unparseable brand.
    scoped: list[str] = []
    for slug in slugs:
        try:
            if load_brand(slug, kits_dir=root).brand.org == org:
                scoped.append(slug)
        except (BrandNotFound, ValueError):
            continue
    return scoped


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
    root = kits_dir or DEFAULT_KITS_DIR
    raw = _read_yaml(slug, root)
    parent_slug = (raw.get("brand") or {}).get("parent")
    if parent_slug:
        parent_raw = _read_yaml(parent_slug, root)
        raw = _merge(parent_raw, raw)
    brand = Brand.model_validate(raw)
    # Tenant guard: a cross-tenant read is a 404, never a 403 leak. When the
    # request is scoped to an org, a brand owned by a different org reads as
    # "no such brand" -- the caller cannot even tell it exists. Unscoped
    # (current_org is None: CLI / MCP / tests) this never fires.
    org = get_current_org()
    if org is not None and brand.brand.org != org:
        raise BrandNotFound(f"brand {slug!r} not found")
    return brand


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
