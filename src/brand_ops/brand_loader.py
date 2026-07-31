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


def current_kits_dir() -> Path:
    """The kits root for the active request: the per-workspace subtree when scoped.

    Tenant brand files live at ``brand-kits/<workspace_id>/<slug>/`` so each
    workspace is physically isolated on disk: a brand read can never resolve a
    path into another tenant's subtree (defense in depth beneath the visibility
    gate), and two tenants may hold the same brand slug without colliding. When
    there is no active workspace (the CLI, the stdio MCP, tests, AUTH off) this is
    the global ``brand-kits/`` root, preserving the original flat behavior. Every
    reader and writer resolves its root through here (via ``kits_dir or
    current_kits_dir()``), so per-request tenant scoping is automatic at one
    boundary rather than threaded through each caller.
    """
    ws = get_current_workspace_id()
    return DEFAULT_KITS_DIR / ws if ws else DEFAULT_KITS_DIR


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


def safe_subpath(root: Path, *parts: str) -> Path | None:
    """Confine a join of caller-supplied path segments under ``root``.

    The brand ``slug`` is validated by ``_validate_slug`` at the brand-read
    boundary, but the catalog/compliance/mark readers also join other
    caller-influenced segments (asset type, kind, file name, mark) onto the kits
    root. This is the one guard for those: each ``part`` must be a single safe
    path segment (a non-empty str, not absolute, no separator, no parent ref, no
    NUL), and the fully resolved join must stay under ``root.resolve()`` so a
    crafted segment or a symlink cannot escape one tenant's subtree into
    another's. Returns the resolved path, or ``None`` when any part is unsafe or
    the result escapes; the caller turns ``None`` into a 404 / skip / empty, never
    a read or write outside the tree. Mirrors the guard the byte-serving routes
    already apply, centralized so every reader and writer shares one rule.
    """
    base = root.resolve()
    p = base
    for part in parts:
        if (
            not isinstance(part, str)
            or not part
            or part in (".", "..")
            or "/" in part
            or "\\" in part
            or "\x00" in part
            or Path(part).is_absolute()
        ):
            return None
        p = p / part
    resolved = p.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        return None
    return resolved


def list_brands(kits_dir: Path | None = None) -> list[str]:
    root = kits_dir or current_kits_dir()
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


def _brand_yaml_path(slug: str, kits_dir: Path) -> Path:
    # Validate before the path is built: this is the chokepoint both load_brand
    # and the parent-merge recursion call, so confining the slug here confines
    # every brand read in the package against traversal.
    _validate_slug(slug)
    return kits_dir / slug / "brand.yaml"


def _read_yaml(slug: str, kits_dir: Path) -> dict:
    path = _brand_yaml_path(slug, kits_dir)
    if not path.exists():
        available = ", ".join(list_brands(kits_dir)) or "(none)"
        raise BrandNotFound(f"brand {slug!r} not found. Available: {available}")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Parsed-brand cache (mtime-keyed)
# ---------------------------------------------------------------------------
#
# ``load_brand`` is on every list and director turn, and re-parsed brand.yaml
# from disk each time. The parse (YAML load + Pydantic validation + the
# parent merge) is pure given the file bytes, so we memoize the validated
# ``Brand`` keyed on the RESOLVED brand.yaml path string plus its
# ``st_mtime_ns``. An edit changes the mtime and so misses the cache naturally;
# two workspaces holding the same slug resolve to different absolute paths and
# so never collide. A sub-brand's key also folds in its parent file's
# path+mtime, so editing either the child or the parent invalidates the merged
# result. The tenant allow-list gate in ``load_brand`` still runs on every call
# (only the parse is cached), and an unscoped caller is unaffected. The cache is
# unbounded but a deploy holds a bounded set of brands; ``clear_brand_cache``
# exists for an explicit reset (tests, or a future in-process writer).
_brand_cache: dict[tuple, Brand] = {}


def clear_brand_cache() -> None:
    """Drop every memoized parsed Brand.

    ``load_brand`` already invalidates per file via ``st_mtime_ns``, so this is
    only needed for a hard reset: a test that mutates a brand.yaml in place and
    wants the parser re-run regardless of timestamp granularity, or a future
    in-process writer in this module. The on-disk writers (clone, intake, admin)
    live in other modules and rely on the mtime key, not this hook.
    """
    _brand_cache.clear()


def _mtime_key(path: Path) -> tuple[str, int] | None:
    """``(resolved_path_str, st_mtime_ns)`` for a brand.yaml, or None if absent.

    None when the file does not exist (or is unstattable), which makes the
    caller fall through to the uncached read path that raises ``BrandNotFound``
    with the available-brands hint, preserving the original error exactly.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path), st.st_mtime_ns)


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
    # this never fires. The gate runs on every call; only the parse below is
    # cached.
    allowed = get_allowed_brands()
    if allowed is not None and slug not in allowed:
        raise BrandNotFound(f"brand {slug!r} not found")
    root = kits_dir or current_kits_dir()

    # Cache fast path: when the brand.yaml is unchanged since we last parsed it
    # (same resolved path + st_mtime_ns), return the memoized Brand. A sub-brand
    # also stores its parent's path+mtime; a hit re-stats only the parent to
    # confirm the merge inputs are still current. Any miss (file changed, parent
    # changed, never seen, or unstattable) falls through to the full parse.
    child_path = _brand_yaml_path(slug, root)
    child_key = _mtime_key(child_path)
    if child_key is not None:
        cached = _brand_cache.get(child_key)
        if cached is not None:
            parent_key, brand_obj = cached
            if parent_key is None or _mtime_key(Path(parent_key[0])) == parent_key:
                return brand_obj

    raw = _read_yaml(slug, root)
    parent_slug = (raw.get("brand") or {}).get("parent")
    parent_key = None
    if parent_slug:
        parent_raw = _read_yaml(parent_slug, root)
        raw = _merge(parent_raw, raw)
        parent_key = _mtime_key(_brand_yaml_path(parent_slug, root))
    brand_obj = Brand.model_validate(raw)

    # Store only when we have a stable child key. ``child_key`` is re-read here
    # rather than reused so the cached mtime matches the bytes we just parsed,
    # closing the narrow window where the file changed between the stat above
    # and the read.
    child_key = _mtime_key(child_path)
    if child_key is not None:
        _brand_cache[child_key] = (parent_key, brand_obj)
    return brand_obj


# Raster (and SVG) suffixes accepted as brand reference / character-ref images.
_REF_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
)
# Hard cap on reference images handed to image backends (Gemini allows 14).
_MAX_CHARACTER_REFS = 14


def _kit_base(slug: str, kits_dir: Path | None = None) -> Path:
    """Resolved on-disk root for one brand's kit directory."""
    root = kits_dir or current_kits_dir()
    return (root / slug).resolve()


def _resolve_kit_relative(base: Path, ref: str) -> Path | None:
    """Resolve a kit-relative ref under ``base``, or None if it escapes / is unsafe.

    Accepts multi-segment relative paths (``imported/logo/mark.png``). Absolute
    paths, empty strings, parent-dir walks, and NUL bytes are refused so a
    misconfigured brand.yaml cannot read outside the kit.
    """
    if not isinstance(ref, str) or not ref.strip() or "\x00" in ref:
        return None
    candidate = Path(ref)
    if candidate.is_absolute():
        return None
    # Refuse any ``..`` segment so ``foo/../../etc/passwd`` cannot escape.
    if ".." in candidate.parts:
        return None
    try:
        resolved = (base / candidate).resolve()
    except (OSError, RuntimeError):
        return None
    if resolved != base and not resolved.is_relative_to(base):
        return None
    return resolved


def audit_reference_images(
    slug: str, brand: Brand, kits_dir: Path | None = None
) -> dict[str, list[str] | dict[str, str]]:
    """Classify every declared reference path for Studio / API surfaces.

    Returns a dict with:
      * ``declared`` — role -> path string as written in brand.yaml
      * ``ok`` — roles whose kit-relative path exists under the brand kit
      * ``missing`` — kit-relative paths that do not exist on disk
      * ``invalid_absolute`` — absolute workstation / host paths (never used)
      * ``invalid_escape`` — relative paths that escape the kit root

    Generation and composition only consume ``ok`` roles. Absolute paths used to
    resolve "missing" silently when the file was absent on the host; they are
    now always flagged so misconfiguration is impossible to miss.
    """
    base = _kit_base(slug, kits_dir)
    declared: dict[str, str] = {
        str(role): str(path) for role, path in brand.reference_images.items()
    }
    ok: list[str] = []
    missing: list[str] = []
    invalid_absolute: list[str] = []
    invalid_escape: list[str] = []

    for role, ref in brand.reference_images.items():
        role_s = str(role)
        if not isinstance(ref, str) or not ref.strip():
            missing.append(role_s)
            continue
        candidate = Path(ref)
        if candidate.is_absolute():
            # Absolute paths are never composition inputs. Flag loudly even if
            # the file happens to exist on this host (it will not on the server).
            invalid_absolute.append(role_s)
            continue
        resolved = _resolve_kit_relative(base, ref)
        if resolved is None:
            invalid_escape.append(role_s)
            continue
        if resolved.exists():
            ok.append(role_s)
        else:
            missing.append(role_s)

    return {
        "declared": declared,
        "ok": ok,
        "missing": missing,
        "invalid_absolute": invalid_absolute,
        "invalid_escape": invalid_escape,
    }


def resolve_reference_images(
    slug: str, brand: Brand, kits_dir: Path | None = None
) -> tuple[dict[str, Path], list[str]]:
    """Return (existing kit-relative refs by role, missing-or-invalid role names).

    A missing or invalid reference is a warning, not an error: composition mode
    is optional. Absolute workstation paths are never opened (they land in the
    missing list so every surface that already surfaces ``missing`` lights up).
    """
    base = _kit_base(slug, kits_dir)
    found: dict[str, Path] = {}
    missing: list[str] = []
    for role, ref in brand.reference_images.items():
        if not isinstance(ref, str) or not ref.strip():
            missing.append(role)
            continue
        if Path(ref).is_absolute():
            # Do not follow absolute host paths — they silently failed for months
            # when brand.yaml pointed at a Mac workstation path that does not
            # exist on the server.
            missing.append(role)
            continue
        resolved = _resolve_kit_relative(base, ref)
        if resolved is None:
            missing.append(role)
            continue
        if resolved.exists():
            found[role] = resolved
        else:
            missing.append(role)
    return found, missing


def resolve_character_references(
    slug: str, brand: Brand, kits_dir: Path | None = None
) -> list[Path]:
    """Prefer the kit's character reference set for agent-avatar generation.

    Looks under ``brand-kits/<slug>/refs/`` first (the dedicated character /
    mascot reference directory). When that directory has images, those are the
    only refs returned — logo ``reference_images`` stay out of the avatar path
    so a logo mark cannot dominate the mascot style. When ``refs/`` is empty or
    missing, falls back to the brand's declared (and resolvable) reference
    images so generation still has something to lock onto.
    """
    base = _kit_base(slug, kits_dir)
    refs_dir = base / "refs"
    if refs_dir.is_dir():
        files = sorted(
            p
            for p in refs_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _REF_IMAGE_SUFFIXES
        )
        if files:
            return files[:_MAX_CHARACTER_REFS]
    found, _missing = resolve_reference_images(slug, brand, kits_dir)
    # Prefer raster refs for edit models; SVG often fails as a reference part.
    ordered = sorted(
        found.values(),
        key=lambda p: (0 if p.suffix.lower() != ".svg" else 1, p.name),
    )
    return ordered[:_MAX_CHARACTER_REFS]
