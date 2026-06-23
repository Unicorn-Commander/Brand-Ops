"""REST routes over the generation engine.

Every route is a thin adapter: it validates input, calls ``engine.generate_asset``
or a loader, and maps domain results and errors onto HTTP. The engine is the one
shared entry point the CLI, the MCP server, and the autonomy agents also use, so
the human path and the agent path can never drift. This module calls no provider
SDK directly and reads no secret; backends self-report health and a missing key
becomes a clean 422, never a 500.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlencode

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy.exc import SQLAlchemyError

from .. import agent_identity, engine, governance, retention, share_meta, voices
from ..activity import append_event, read_events
from ..agents.brand_intake import BrandIntakeAgent
from ..agents.brand_research import _slugify
from ..agents.director import DirectorAgent
from ..agents.planner import GenerationPlanner
from ..agents.registry import AGENT_REGISTRY
from ..asset_types import ASSET_TYPES, get_asset_spec
from ..backends.base import (
    BackendError,
    BackendNotConfigured,
    EntitlementDenied,
    QuotaExceeded,
)
from ..brand_clone import clone_brand, create_brand_from_draft
from ..brand_loader import (
    BrandNotFound,
    current_kits_dir,
    get_allowed_brands,
    get_current_workspace_id,
    list_brands,
    load_brand,
    safe_subpath,
    workspace_query_suffix,
)
from ..compliance import (
    build_rules,
    build_tokens,
    check_asset,
    check_text_target,
    transition,
)
from ..config import get_settings
from ..db import repository as repo
from ..db.base import get_sessionmaker, workspace_session
from ..db.tenancy import record_brand, resolve_context
from ..idempotency import begin_sync as idem_begin
from ..idempotency import complete_sync as idem_complete
from ..idempotency import fingerprint as idem_fingerprint
from ..idempotency import release_sync as idem_release
from ..models import Brand
from ..templates import list_templates
from .agents import UnknownAgent, run_agent
from .auth import Principal, agent_scopes_satisfied, require_principal, require_role
from .catalog import _coerce_record, read_assets
from .guidelines import build_guidelines
from .schemas import (
    ActivityEvent,
    ActivityList,
    AgentGovernanceList,
    AgentGovernanceOut,
    AgentGovernanceUpdate,
    AgentIdentityList,
    AgentIdentityOut,
    AgentInfo,
    AgentList,
    AgentRunResult,
    AgentTeam,
    ApproveRequest,
    ApproveResult,
    AssetList,
    AssetRecord,
    AssetTypeList,
    AssetTypeOut,
    BrandColorsOut,
    BrandComplianceRollup,
    BrandDetail,
    BrandGuidelines,
    BrandList,
    BrandRules,
    BrandSummary,
    BrandTokens,
    BrandTypographyOut,
    BrandVisualOut,
    BrandVoiceOut,
    CloneBrandRequest,
    ComplianceAssetRow,
    ComplianceCheckRequest,
    ComplianceResult,
    ComplianceRollupSummary,
    CreateAgentIdentityRequest,
    DeleteResult,
    DirectorChatRequest,
    DirectorReply,
    GenerateCardRequest,
    GeneratedFile,
    GenerateRequest,
    GenerateResult,
    IntakeDraft,
    IntakeRequest,
    IntakeResult,
    MeOut,
    MeWorkspacesOut,
    PlanGenerationReply,
    PlanGenerationRequest,
    PromptPreview,
    SetAgentVoiceRequest,
    SharePreview,
    SharePreviewImage,
    TemplateList,
    TransitionRequest,
    TransitionResult,
    UpdateAgentRequest,
    UploadResult,
    VoiceCatalog,
    VoiceOption,
    VoicePreviewRequest,
    WorkspaceOut,
)
from .upload import MAX_UPLOAD_BYTES, UploadError, save_upload

router = APIRouter(prefix="/api", tags=["brand-ops"])

_log = logging.getLogger("brand_ops.api.routes")

# 422 as a literal. The named constant was renamed across Starlette versions
# (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT); the integer is stable and
# version-proof across the pinned fastapi range. Used for "valid request,
# unprocessable input" (unknown asset/brand-shape, backend needs a key).
_HTTP_422 = 422

# Auth is applied to every route as a dependency. It resolves to the anonymous
# principal when AUTH_REQUIRED is false, so the surface stays uniform whether or
# not auth is on.
_Auth = Annotated[Principal, Depends(require_principal)]
# Owner-gated dependency for the mutating, governance, and brand/identity-creation
# routes: RLS still isolates the tenant, this adds the in-workspace authorization
# the audit found unenforced (a member was operationally an owner). The unscoped
# CLI / stdio MCP / tests path stays exempt (see require_role).
_Owner = Annotated[Principal, Depends(require_role("owner"))]

# Extensions a browser safely renders as a picture in an <img> tag: these stay
# inline so the GUI keeps working. Anything else (an SVG above all, which can
# carry <script>) is served as a forced download, so a stored-XSS payload in an
# uploaded asset cannot execute on a shared origin once auth is on.
_INLINE_RASTER_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


# A served asset is private to its workspace; cache it in the browser but make it
# revalidate (so an overwrite at the same path is picked up) -- the ETag turns that
# revalidation into a cheap 304 instead of a full re-download.
_ASSET_CACHE_CONTROL = "private, max-age=300"


def _file_etag(stat: os.stat_result) -> str:
    """A stat-derived ETag for one file, matching Starlette's FileResponse scheme.

    The same ``"md5(mtime-size)"`` Starlette would set, derived from a stat the
    caller takes once and also hands to the FileResponse, so the validator and the
    served representation's metadata come from the SAME stat (no skew if the file is
    overwritten between two separate stats). Strong validator (no ``W/`` prefix).
    """
    base = f"{stat.st_mtime}-{stat.st_size}"
    return '"' + hashlib.md5(base.encode(), usedforsecurity=False).hexdigest() + '"'


def _etag_matches(if_none_match: str, etag: str) -> bool:
    """True when an ``If-None-Match`` header matches our ETag (so we may 304).

    Honors ``*`` (matches anything) and a comma-separated list, and ignores a weak
    ``W/`` prefix on either side per RFC 9110's weak comparison, which is the right
    comparison for a conditional GET.
    """
    if if_none_match.strip() == "*":
        return True

    def _norm(tag: str) -> str:
        tag = tag.strip()
        return tag[2:] if tag.startswith("W/") else tag

    target = _norm(etag)
    return any(_norm(c) == target for c in if_none_match.split(","))


def _safe_file_response(
    path: Path, etag: str | None = None, stat_result: os.stat_result | None = None
) -> FileResponse:
    """Serve a file from disk with sniff-proof, XSS-safe headers.

    ``X-Content-Type-Options: nosniff`` is always set so a browser honors the
    declared Content-Type rather than sniffing the bytes into HTML. A raster
    image (png/jpeg/webp/gif) is served inline so ``<img>`` tags render it; any
    other type — notably ``.svg``, which can embed script — gets
    ``Content-Disposition: attachment`` so the browser downloads it instead of
    rendering (and executing) it inline. The decision is on the stored
    extension, which the upload path derives from the validated content type,
    not from the untrusted uploaded name.

    When an ``etag`` is given it is set explicitly along with a revalidating
    ``Cache-Control`` so the 200 response carries the identical validator the 304
    path compares against.
    """
    headers = {"X-Content-Type-Options": "nosniff"}
    if path.suffix.lower() not in _INLINE_RASTER_EXTS:
        headers["Content-Disposition"] = "attachment"
    if etag is not None:
        headers["ETag"] = etag
        headers["Cache-Control"] = _ASSET_CACHE_CONTROL
    # Passing the caller's stat through means the content-length/last-modified
    # FileResponse emits come from the same stat the ETag did, not a fresh one.
    return FileResponse(path, headers=headers, stat_result=stat_result)


def _ensure_brand_visible(brand: str) -> None:
    """404 a brand outside the request's workspace allow-list (cross-tenant guard).

    The brand-detail, guidelines, agent team/list/create, and compliance routes
    already gate by going through ``load_brand``. The routes that resolve a file
    or sidecar path directly -- serving asset/avatar bytes, moving an asset's
    lifecycle -- call this so they return the same scoped 404 instead of touching
    another workspace's bytes. No-op when unscoped (CLI / stdio MCP / tests).
    """
    allowed = get_allowed_brands()
    if allowed is not None and brand not in allowed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="brand not found"
        )


def _mark_url(brand: Brand) -> str | None:
    """Build the /api/assets/file URL for the brand's canonical mark, or None.

    Mirrors the asset URL the catalog builds in api/catalog.py so the same file
    resolves the same way; the file component is percent-encoded with
    ``quote`` because brand mark filenames can carry spaces or other reserved
    characters.
    """
    mark = brand.brand.mark
    if mark is None:
        return None
    return (
        f"/api/assets/file?brand={brand.brand.slug}"
        f"&asset={mark.asset_type}"
        f"&file={quote(mark.file)}"
        f"&kind={mark.kind}"
        f"{workspace_query_suffix()}"
    )


def _generated_file_url(brand: str, asset: str, path: str) -> str | None:
    """Build the /api/assets/file URL for a freshly generated file, or None.

    Identical to the URL api/catalog.py:_coerce_record builds for a catalog
    record (same four params, same urlencode), so a just-generated file resolves
    through GET /api/assets/file exactly as the same file later does from the
    Library -- and the route's auth + traversal guard apply unchanged. The file
    component is the basename of the engine's output path; kind is always
    "generated" (the engine writes generated files into the generated/ tree).
    Returns None when any addressing part is missing.
    """
    file_name = Path(path).name
    if not (brand and asset and file_name):
        return None
    return (
        "/api/assets/file?"
        + urlencode(
            {"brand": brand, "asset": asset, "file": file_name, "kind": "generated"}
        )
        + workspace_query_suffix()
    )


def _brand_summary(brand: Brand) -> BrandSummary:
    meta = brand.brand
    return BrandSummary(
        slug=meta.slug,
        display_name=meta.display_name,
        tagline=meta.tagline,
        parent=meta.parent,
        mark_url=_mark_url(brand),
        # Carry the resolved palette so the list-only brand switcher can theme by
        # the real colors. Same source BrandDetail uses (the loaded brand's visual
        # colors), so the list and the detail can never disagree on a brand's hex.
        colors=BrandColorsOut(**brand.visual.colors.model_dump()),
    )


def _brand_detail(brand: Brand) -> BrandDetail:
    meta = brand.brand
    visual = brand.visual
    return BrandDetail(
        slug=meta.slug,
        display_name=meta.display_name,
        tagline=meta.tagline,
        parent=meta.parent,
        mark_url=_mark_url(brand),
        description=meta.description,
        visual=BrandVisualOut(
            colors=BrandColorsOut(**visual.colors.model_dump()),
            typography=BrandTypographyOut(**visual.typography.model_dump()),
            style_keywords=list(visual.style_keywords),
        ),
        voice=BrandVoiceOut(
            tone=list(brand.voice.tone),
            forbidden_phrases=list(brand.voice.forbidden_phrases),
            voice_id=brand.voice.voice_id,
            voice_engine=brand.voice.voice_engine,
        ),
        prompt_assets=sorted(brand.prompts),
        reference_roles=sorted(brand.reference_images),
    )


# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


@router.get("/me", response_model=MeOut)
def get_me(principal: _Auth) -> MeOut:
    """Who am I: the verified principal behind this request.

    Drives the user menu and the Settings account card. ``name``/``email``
    come from the token claims when the issuer provided them; the anonymous
    principal (AUTH_REQUIRED off, e.g. local dev) reports itself honestly.
    """
    claims = principal.claims or {}
    settings = get_settings()
    logout = (
        f"{settings.keycloak_realm_url}/protocol/openid-connect/logout"
        f"?client_id={settings.keycloak_client_id}"
        if not principal.anonymous
        else None
    )
    return MeOut(
        uc_uid=principal.uc_uid,
        email=claims.get("email"),
        name=claims.get("name") or claims.get("preferred_username"),
        workspace_id=principal.workspace_id,
        workspace=principal.workspace_slug,
        role=principal.role,
        org=principal.workspace_slug,
        issuer=principal.issuer,
        anonymous=principal.anonymous,
        scopes=list(principal.scopes),
        logout_url=logout,
    )


@router.get("/me/workspaces", response_model=MeWorkspacesOut)
async def get_my_workspaces(principal: _Auth) -> MeWorkspacesOut:
    """The caller's workspaces (the switcher's data) and the active one.

    Anonymous / unscoped callers (local dev, the CLI) have no memberships and
    get an empty list, so the GUI simply shows no switcher. The active workspace
    is whichever the request resolved to (the ``X-Workspace-Id`` the GUI sent, or
    the caller's default membership).
    """
    if principal.anonymous:
        return MeWorkspacesOut()
    ctx = await resolve_context(
        principal.uc_uid, requested_workspace_id=principal.workspace_id
    )
    return MeWorkspacesOut(
        active_workspace_id=ctx.workspace_id,
        workspaces=[
            WorkspaceOut(
                workspace_id=m.workspace_id,
                slug=m.slug,
                role=m.role,
                is_default=m.is_default,
            )
            for m in ctx.memberships
        ],
    )


async def _index_brand(principal: Principal, brand: Brand) -> None:
    """Record a just-created brand in the DB so it joins its workspace catalog.

    Only when the request is workspace-scoped (an authed caller in a workspace);
    unscoped creates (CLI / local) stay file-only, matching their unscoped reads.
    A DB hiccup is logged, not fatal: the file exists and the boot import will
    pick the brand up, so the create still succeeds.
    """
    if not principal.workspace_id:
        return
    meta = brand.brand
    try:
        await record_brand(
            principal.workspace_id,
            slug=meta.slug,
            display_name=meta.display_name,
            parent_slug=meta.parent,
            tagline=meta.tagline,
            created_by=principal.uc_uid,
        )
    except SQLAlchemyError:
        _log.warning("brand %s created in files but not indexed in DB", meta.slug)


@router.get("/brands", response_model=BrandList)
def get_brands(_: _Auth) -> BrandList:
    """List the seeded brands, each in summary form."""
    summaries: list[BrandSummary] = []
    for slug in list_brands():
        try:
            summaries.append(_brand_summary(load_brand(slug)))
        except (BrandNotFound, ValueError):
            # A brand that fails validation should not break the whole list.
            continue
    return BrandList(brands=summaries)


@router.get("/brands/{slug}", response_model=BrandDetail)
def get_brand(slug: str, _: _Auth) -> BrandDetail:
    """Return one brand in full, including visual, voice, and prompt coverage."""
    try:
        brand = load_brand(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=_HTTP_422, detail=str(exc)
        ) from None
    return _brand_detail(brand)


@router.post("/brands/{slug}/clone", response_model=BrandDetail)
async def clone_brand_route(
    slug: str, body: CloneBrandRequest, principal: _Owner
) -> BrandDetail:
    """Clone an existing brand into a new sub-brand or a distinct seeded brand.

    Creates ``brand-kits/<new_slug>/brand.yaml`` from ``slug``. With
    ``as_child`` true (the default) the new brand is a minimal child that
    inherits ``slug``'s visual and voice through the loader's parent merge; with
    it false the source's resolved visual, voice, and prompts are copied in and
    ``parent`` is null. The source's canonical mark is never copied, so the
    returned brand's ``mark_url`` is null until a logo is imported.

    The response is the newly created brand in the same full ``BrandDetail``
    shape GET /api/brands/{slug} serves. An unknown source ``slug`` is a 404, a
    ``new_slug`` that already exists is a 409, and an invalid ``new_slug``
    (must be lowercase letters, digits, and hyphens) is a 422.
    """
    try:
        brand = clone_brand(
            slug,
            body.new_slug,
            body.display_name,
            as_child=body.as_child,
        )
    except BrandNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from None
    except FileExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
    except (ValueError, FileNotFoundError) as exc:
        # ValueError is a bad new_slug; FileNotFoundError covers a source the
        # loader signals via the stdlib error rather than BrandNotFound.
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    await _index_brand(principal, brand)
    return _brand_detail(brand)


def _parent_identity(slug: str) -> dict[str, Any] | None:
    """Project a parent brand into the flat identity dict the intake agent reads.

    Returns ``{slug, display_name, colors, voice, style_keywords}`` so the
    Director can draft a sub-brand consistent with the parent's palette and
    voice. A parent that does not exist or fails validation returns None, and the
    draft proceeds without parent context (the route still records the parent on
    the new brand). Best-effort: a parent read never fails the intake.
    """
    try:
        parent = load_brand(slug)
    except (BrandNotFound, ValueError):
        return None
    colors = parent.visual.colors
    return {
        "slug": parent.brand.slug,
        "display_name": parent.brand.display_name,
        "colors": {
            "primary": colors.primary,
            "accent": colors.accent,
            "background": colors.background,
            "foreground": colors.foreground,
            "muted": colors.muted,
        },
        "voice": {"tone": list(parent.voice.tone)},
        "style_keywords": list(parent.visual.style_keywords),
    }


@router.post("/brands/intake", response_model=IntakeResult)
async def intake_brand_route(body: IntakeRequest, principal: _Owner) -> IntakeResult:
    """Draft a brand identity with the Director and create the brand.

    The single real-intake door: from a brand ``name`` and whatever the operator
    can say (``description``, ``url``, an optional ``parent`` for a sub-brand),
    the Director drafts a full identity (palette, typography, voice, style
    keywords, tagline, description) through the LLM gateway, and the route writes
    a complete, valid ``brand-kits/<slug>/brand.yaml`` from it. The slug is
    derived from the name (lowercase letters, digits, hyphens). When ``parent``
    is set the draft is kept consistent with the parent's palette and voice and
    the new brand records the parent. ``mode=onboard`` with a ``url`` drafts from
    the url and name as hints and notes that deep live-site capture needs Brigade.

    The response is the newly created brand in the full ``BrandDetail`` shape GET
    /api/brands/{slug} serves, plus the ``draft`` the Director produced (for the
    chat to narrate) and honest ``notes``.

    This route never 500s on an LLM problem: when the gateway is unavailable or
    returns an unusable draft, the agent degrades to a neutral on-theme default
    identity and the brand is still created, with a note saying it is a starting
    point to refine. A name that yields no valid slug is a 422; a slug that
    already exists is a 409.
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=_HTTP_422, detail="name is required")

    # Derive the slug from the name and validate it up front, so a name with no
    # usable slug is a clean 422 before any drafting or file work. _slugify never
    # raises and always returns a ^[a-z0-9-]+$ value; an all-symbol name
    # collapses to the literal "brand", so the guard is on the original having
    # at least one letter or digit rather than on _slugify's safety net.
    if not _has_sluggable_char(name):
        raise HTTPException(
            status_code=_HTTP_422,
            detail=(
                f"name {name!r} does not yield a valid slug: use letters or "
                "digits so a lowercase-hyphen slug can be derived"
            ),
        )
    slug = _slugify(name)

    # Fail fast on a duplicate before spending an LLM turn drafting an identity
    # we could not write anyway.
    if (current_kits_dir() / slug).exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"brand {slug!r} already exists",
        )

    parent = (body.parent or "").strip() or None
    parent_identity = _parent_identity(parent) if parent else None

    # Draft the identity. The agent never raises for an LLM problem (it falls
    # back to a neutral default with a note), but we still wrap the call so any
    # unexpected error degrades to the same default path rather than a 500.
    notes: list[str]
    try:
        # Thread metering context onto the intake agent: when the caller is
        # workspace-scoped, its drafting LLM turn records a best-effort spend
        # event against specialist/brand_intake. The fallback below
        # (default_draft_with_note) makes no LLM call, so it stays unmetered.
        # brand_slug is left None (the brand does not exist yet at intake time).
        intake_agent = BrandIntakeAgent(
            workspace_id=principal.workspace_id,
            agent_kind="specialist",
            agent_ref="brand_intake",
        )
        draft, notes = await intake_agent.draft_identity(
            name,
            description=(body.description or "").strip(),
            url=(body.url or "").strip(),
            parent_brand=parent_identity,
            mode=(body.mode or "describe").strip().lower(),
        )
    except Exception:  # noqa: BLE001 - an LLM/agent problem degrades, never a 500
        draft, notes = BrandIntakeAgent().default_draft_with_note(name, parent_identity)

    if parent and parent_identity is None:
        notes.append(
            f"I could not load the parent brand {parent!r}, so I drafted without "
            "its palette as a reference. The new brand still records it as parent."
        )

    # The operator's typed name is the display name, with its original casing.
    # The draft does not carry one (it is an identity, not a name), so stamp it
    # here before the write rather than letting the writer fall back to the slug.
    draft["display_name"] = name

    # Write the brand from the draft. A duplicate is a 409 (the pre-check makes
    # this rare but a concurrent create is still possible); a bad slug or an
    # invalid drafted color is a 422.
    try:
        brand = create_brand_from_draft(slug, draft, parent=parent)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None

    _emit_intake_event(brand.brand.slug, name)
    await _index_brand(principal, brand)

    return IntakeResult(
        brand=_brand_detail(brand),
        draft=IntakeDraft(
            palette=BrandColorsOut(**draft["palette"]),
            typography=BrandTypographyOut(**draft["typography"]),
            voice_tone=list(draft.get("voice_tone", [])),
            style_keywords=list(draft.get("style_keywords", [])),
            tagline=str(draft.get("tagline", "")),
            rationale=str(draft.get("rationale", "")),
        ),
        notes=notes,
    )


def _has_sluggable_char(name: str) -> bool:
    """True when the name has at least one letter or digit to seed a slug.

    ``_slugify`` collapses an all-symbol name to the literal "brand" (a valid
    slug), which would silently create a brand named "brand" from junk input.
    The route rejects that case as a 422, so this guards the original.
    """
    return any(ch.isalnum() for ch in name)


def _emit_intake_event(slug: str, name: str) -> None:
    """Append one activity event for a brand intake. Best-effort, never raises."""
    try:
        from ..activity import append_event

        append_event(
            "iris",
            "agent",
            f"created brand {name}".strip(),
            brand=slug,
            target=None,
            note=None,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the intake
        pass


# ---------------------------------------------------------------------------
# Compliance: tokens, rules, and the deterministic checker
# ---------------------------------------------------------------------------


def _load_brand_or_404(slug: str) -> Brand:
    """Load a brand, mapping a missing brand to 404 and a bad shape to 422."""
    try:
        return load_brand(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None


@router.get("/brands/{slug}/tokens", response_model=BrandTokens)
def get_brand_tokens(slug: str, _: _Auth) -> BrandTokens:
    """Return the brand's machine-readable design tokens.

    Semantic color and typography tokens plus a ready-to-paste ``css`` block of
    ``:root`` custom properties. This is the brand's visual truth as data, for
    agents and the rest of the suite to style and check against.
    """
    brand = _load_brand_or_404(slug)
    return BrandTokens.model_validate(build_tokens(brand))


@router.get("/brands/{slug}/rules", response_model=BrandRules)
def get_brand_rules(slug: str, _: _Auth) -> BrandRules:
    """Return the brand's deterministic rules manifest.

    The allowed palette, color tolerance, gradient axis, banned and required
    copy terms, voice tone, and the human-judged manual rules. This is exactly
    what the compliance checker scores an asset or a piece of copy against.
    """
    brand = _load_brand_or_404(slug)
    return BrandRules.model_validate(build_rules(brand))


@router.get("/brands/{slug}/guidelines", response_model=BrandGuidelines)
def get_brand_guidelines(slug: str, _: _Auth) -> BrandGuidelines:
    """Return the brand's full guidelines document: the assembled brand book.

    One read that gathers the identity, the design tokens (colors + typography),
    the deterministic rules manifest, the voice, the canonical mark plus its logo
    lockups, and every catalog asset grouped by type in the asset-type registry
    order. Nothing is computed live beyond reading the same on-disk catalog the
    asset list reads; ``generated_at`` is left null. A missing brand is a 404.
    """
    try:
        doc = build_guidelines(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    return BrandGuidelines.model_validate(doc)


def _latest_og_image(slug: str) -> SharePreviewImage | None:
    """The brand's newest og_image catalog record (to preview + download), or None.

    Reads the same scoped catalog the Library reads, filtered to og_image; best
    effort, so a read failure or a brand with no share image yet simply yields
    None (the kit is still returned, just without a hosted-image reference).
    """
    try:
        records = [r for r in read_assets(brand=slug, asset="og_image") if r.url]
    except Exception:  # noqa: BLE001 - a catalog hiccup must not break the kit
        return None
    if not records:
        return None
    records.sort(key=lambda r: r.created_at or "", reverse=True)
    newest = records[0]
    width, height = (list(newest.size) + [1200, 630])[:2]
    return SharePreviewImage(
        asset_url=newest.url, file=newest.file, width=width, height=height
    )


@router.get("/brands/{slug}/share-preview", response_model=SharePreview)
def get_brand_share_preview(
    slug: str,
    _: _Auth,
    canonical_url: str | None = None,
    image_url: str | None = None,
) -> SharePreview:
    """Return the brand's social-share / link-preview kit.

    The Open Graph + Twitter Card tags (and a ready-to-paste HTML snippet) a
    brand drops into its OWN site's ``<head>`` so a shared link unfurls into the
    brand's 1200x630 share image. The preview lives on the brand's site, not in
    Brand-Ops, so the canonical URL and the hosted image URL are the brand's to
    supply: pass ``canonical_url`` / ``image_url``, or set the brand's
    ``website`` to fill og:url automatically. Any tag left on an example domain
    is named in ``placeholders``. ``image`` references the brand's og_image in
    the catalog (to preview and download), or is null if none exists yet. A
    missing brand is a 404.
    """
    brand = _load_brand_or_404(slug)
    meta = share_meta.build_share_meta(
        brand, canonical_url=canonical_url, image_url=image_url
    )
    return SharePreview(brand=slug, image=_latest_og_image(slug), **meta)


@router.post("/compliance/check", response_model=ComplianceResult)
def compliance_check(body: ComplianceCheckRequest, _: _Auth) -> ComplianceResult:
    """Score one asset or one piece of copy against a brand's rules.

    Supply ``text`` to check copy, or ``file`` (with ``asset`` and an optional
    ``kind``, default ``generated``) to check a catalog asset's pixels and its
    recorded prompt. The check is fully deterministic — no LLM — so the same
    input always yields the same verdict. A missing brand is a 404; a request
    with neither ``text`` nor ``file`` is a 422.
    """
    brand = _load_brand_or_404(body.brand)
    rules = build_rules(brand)

    if body.text is not None:
        result = check_text_target(body.brand, body.text, rules)
    elif body.file:
        if not body.asset:
            raise HTTPException(
                status_code=_HTTP_422,
                detail="checking a file requires the asset type",
            )
        kind = body.kind or "generated"
        if kind not in ("generated", "imported"):
            raise HTTPException(
                status_code=_HTTP_422, detail="kind must be generated or imported"
            )
        result = check_asset(body.brand, body.file, body.asset, kind, rules)
    else:
        raise HTTPException(
            status_code=_HTTP_422,
            detail="provide either text or file to check",
        )

    return ComplianceResult.model_validate(result)


def _asset_verdict(slug: str, record, rules: dict) -> tuple[str, int, list[str]]:
    """The (status, score, violations) for one catalog asset.

    Prefers the verdict already stamped into the asset's sidecar (the
    ``{status, score, violations}`` summary ``stamp_compliance`` wrote at
    creation time, surfaced by ``read_assets`` as ``record.compliance``), so the
    brand rollup does not re-decode an image whose verdict is already on disk.
    Only an asset with no stamp (a kit made before the lifecycle gate, or one
    that degraded to a null stamp) is re-checked the original way with
    ``check_asset``. The deterministic engine makes the stamp and a fresh check
    identical for unchanged bytes, so this is a pure cache, not a behavior
    change.
    """
    stamp = record.compliance
    if stamp is not None and isinstance(stamp.status, str) and stamp.status:
        return stamp.status, int(stamp.score), list(stamp.violations)
    result = check_asset(slug, record.file, record.asset_type, record.kind, rules)
    return result["status"], int(result["score"]), list(result["violations"])


def compute_brand_compliance(slug: str) -> BrandComplianceRollup:
    """Score every catalog asset of a brand and roll the verdicts into a summary.

    The shared core of GET /brands/{slug}/compliance, callable from the MCP server
    so the agent path and the HTTP path run the exact same deterministic engine
    (``build_rules`` + ``check_asset`` over the same ``read_assets`` catalog) with
    the same workspace scoping, and can never drift. Reads each asset's
    already-stamped verdict from its sidecar and only re-checks an asset whose
    stamp is missing, so a brand-wide rollup does not re-decode every image on
    every request. Raises ``BrandNotFound`` / ``ValueError`` for an unknown or
    malformed brand, which each caller maps to its own error shape.
    """
    brand = load_brand(slug)
    rules = build_rules(brand)

    counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    rows: list[ComplianceAssetRow] = []
    scores: list[int] = []

    for record in read_assets(brand=slug):
        result_status, result_score, result_violations = _asset_verdict(
            slug, record, rules
        )
        counts[result_status] = counts.get(result_status, 0) + 1
        # A skipped asset (e.g. an SVG) carries no meaningful score; leave it out
        # of the average so vectors don't drag the brand's number down.
        if result_status != "skipped":
            scores.append(result_score)
        rows.append(
            ComplianceAssetRow(
                file=record.file,
                asset_type=record.asset_type,
                kind=record.kind,
                status=result_status,
                score=result_score,
                violations=result_violations,
            )
        )

    avg = round(sum(scores) / len(scores)) if scores else 0
    summary = ComplianceRollupSummary(
        total=len(rows),
        **{"pass": counts["pass"]},
        warn=counts["warn"],
        fail=counts["fail"],
        skipped=counts["skipped"],
        score=avg,
    )
    return BrandComplianceRollup(brand=slug, summary=summary, assets=rows)


@router.get("/brands/{slug}/compliance", response_model=BrandComplianceRollup)
def get_brand_compliance(slug: str, _: _Auth) -> BrandComplianceRollup:
    """Run the compliance check over every catalog asset of the brand.

    Iterates the brand's catalog, scores each asset deterministically, and rolls
    the verdicts into a summary (pass/warn/fail/skipped counts and an average
    score) plus a per-asset row list. The same engine POST /compliance/check
    uses, applied brand-wide.
    """
    try:
        return compute_brand_compliance(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None


# ---------------------------------------------------------------------------
# Asset types
# ---------------------------------------------------------------------------


@router.get("/asset-types", response_model=AssetTypeList)
def get_asset_types(_: _Auth) -> AssetTypeList:
    """List the asset types the engine can produce, with sizing and defaults."""
    items = [
        AssetTypeOut(
            key=spec.key,
            label=spec.label,
            width=spec.size[0],
            height=spec.size[1],
            default_backend=spec.default_backend,
            transparent=spec.transparent,
            notes=spec.notes,
        )
        for spec in ASSET_TYPES.values()
    ]
    return AssetTypeList(asset_types=items)


# ---------------------------------------------------------------------------
# Templates (locked, data-driven layouts the frontend renders from brand tokens)
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=TemplateList)
def get_templates(_: _Auth) -> TemplateList:
    """List the locked templates the Studio renders on-brand from any brand.

    Each template is a data-driven layout, not an image: the frontend draws it on
    a canvas at ``width`` x ``height`` using the active brand's tokens (resolving
    the slot color token names to hex via GET /api/brands/{slug}/tokens), so the
    output is on brand by construction. Slots are positioned by 0..1 fractions to
    scale, editing is limited to text slot content, and a saved render posts to
    POST /api/assets/upload under the template's ``asset_type`` so it lands in the
    library and flows through the compliance gate. Static and unauthenticated-
    same as the asset-type registry; the same set the MCP list_templates serves.
    """
    return TemplateList.model_validate({"templates": list_templates()})


# ---------------------------------------------------------------------------
# Prompt preview (dry run) + generate (live)
# ---------------------------------------------------------------------------


def _validate_asset(asset: str) -> None:
    try:
        get_asset_spec(asset)
    except KeyError as exc:
        raise HTTPException(
            status_code=_HTTP_422, detail=str(exc)
        ) from None


@router.post("/prompt-preview", response_model=PromptPreview)
def prompt_preview(body: GenerateRequest, _: _Auth) -> PromptPreview:
    """Compose the prompt and the output plan without calling any backend.

    This is the no-spend path: a UI or an agent can review the exact prompt,
    backend choice, size, and any copy warnings before committing to a paid
    generation.
    """
    _validate_asset(body.asset)
    try:
        outcome = engine.generate_asset(
            body.brand,
            body.asset,
            variant=body.variant,
            topic=body.topic,
            platform=body.platform,
            backend=body.backend,
            dry_run=True,
        )
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=_HTTP_422, detail=str(exc)
        ) from None

    built = outcome.built
    return PromptPreview(
        brand=body.brand,
        asset=body.asset,
        variant=built.variant,
        backend=built.backend,
        width=built.size[0],
        height=built.size[1],
        transparent=built.transparent,
        prompt=built.text,
        reference_images=[p.name for p in built.reference_images],
        planned_path=str(outcome.planned_path),
        missing_refs=outcome.missing_refs,
        warnings=built.warnings,
    )


@router.post("/generate", response_model=GenerateResult)
def generate(
    body: GenerateRequest,
    _: _Auth,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> GenerateResult:
    """Generate the asset live, persisting the files and the catalog sidecars.

    When the chosen backend has no credentials, the engine raises
    ``BackendNotConfigured``; that becomes an HTTP 422 with a clear needs-key
    body rather than a 500, so a caller knows exactly which key to supply.

    Supply an ``Idempotency-Key`` header to make a retry safe: a repeat with the
    same key returns the original result instead of generating (and charging)
    again; a concurrent repeat gets 409; the same key with different parameters
    gets 422. Without the header the behavior is unchanged.
    """
    _validate_asset(body.asset)

    ws = get_current_workspace_id()
    idem_claimed = False
    if idempotency_key:
        outcome = idem_begin(
            ws, idempotency_key, idem_fingerprint("/api/generate", body.model_dump())
        )
        if outcome.replay is not None:
            return GenerateResult(**outcome.replay)
        if outcome.conflict:
            raise HTTPException(
                status_code=_HTTP_422,
                detail="Idempotency-Key was already used with different parameters",
            )
        if outcome.in_progress:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="a request with this Idempotency-Key is already in progress",
            )
        idem_claimed = outcome.claimed

    try:
        try:
            outcome_gen = engine.generate_asset(
                body.brand,
                body.asset,
                variant=body.variant,
                topic=body.topic,
                platform=body.platform,
                backend=body.backend,
                count=body.count,
                dry_run=False,
            )
        except BrandNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from None
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
        except BackendNotConfigured as exc:
            backend_name = body.backend or get_asset_spec(body.asset).default_backend
            raise HTTPException(
                status_code=_HTTP_422,
                detail={
                    "error": "backend_not_configured",
                    "backend": backend_name,
                    "detail": str(exc),
                },
            ) from None
        except QuotaExceeded as exc:
            # Over the workspace image spend cap (or an agent weekly budget). The
            # request was valid; the tenant is rate/spend limited, so 429.
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
            ) from None
        except EntitlementDenied as exc:
            # Valid request, but this workspace's tier does not include hosted
            # generation (a Free tier = on-device / card paths only). 402 Payment
            # Required is the honest signal: not an auth failure, not a transient
            # rate limit. Placed before the generic BackendError arm so it gets
            # the precise status (EntitlementDenied subclasses BackendError, so
            # without this arm it would fall through to the 502 below). The gate
            # only raises when an operator has turned enforcement on.
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)
            ) from None
        except BackendError as exc:
            # A real provider failure (network, quota, bad response). Surface it as a
            # 502: the request was valid, the upstream backend could not fulfill it.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"backend error: {exc}"
            ) from None

        result = outcome_gen.result
        files = [
            GeneratedFile(
                path=str(p),
                catalog_path=str(p) + ".json",
                url=_generated_file_url(body.brand, body.asset, str(p)),
            )
            for p in outcome_gen.paths
        ]
        response = GenerateResult(
            brand=body.brand,
            asset=body.asset,
            variant=outcome_gen.built.variant,
            backend=result.backend if result else outcome_gen.built.backend,
            model=result.model if result else None,
            mime=result.mime if result else None,
            cost_cents=result.cost_cents if result else None,
            files=files,
            missing_refs=outcome_gen.missing_refs,
            warnings=outcome_gen.built.warnings,
        )
    except BaseException:
        # Any failure (incl. the mapped HTTP errors above) releases the claim so a
        # retry with the same key can re-attempt rather than being stuck replaying
        # a generation that never happened.
        if idem_claimed:
            idem_release(ws, idempotency_key)
        raise

    if idem_claimed:
        idem_complete(ws, idempotency_key, status=200, body=response.model_dump())
    return response


@router.post("/generate-card", response_model=GenerateResult)
def generate_card_route(body: GenerateCardRequest, _: _Auth) -> GenerateResult:
    """Compose an on-brand text card deterministically: no provider key, no spend.

    This is the keyless path that always works: ``og_image`` and ``social_card``
    are rendered from the brand's real fonts, exact colors, and mark, so the copy
    is correct and on brand without any model. It saves into the same catalog as
    /generate (so it shows in the Library and gets a compliance stamp) and returns
    the same shape. An asset with no card layout (e.g. ``logo``) is a 422; an
    unknown or out-of-workspace brand is a 404 (the brand load is workspace
    scoped, same as every other read).
    """
    try:
        outcome = engine.generate_card(
            body.brand,
            body.asset,
            message=body.message,
            mark=body.mark,
            variant=body.variant,
            dry_run=False,
        )
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except (KeyError, ValueError) as exc:
        # Unknown asset type, or an asset with no card layout. Actionable 422.
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None

    result = outcome.result
    files = [
        GeneratedFile(
            path=str(p),
            catalog_path=str(p) + ".json",
            url=_generated_file_url(body.brand, body.asset, str(p)),
        )
        for p in outcome.paths
    ]
    return GenerateResult(
        brand=body.brand,
        asset=body.asset,
        variant=outcome.built.variant,
        backend=result.backend if result else "compose",
        model=result.model if result else None,
        mime=result.mime if result else None,
        cost_cents=result.cost_cents if result else None,
        files=files,
        missing_refs=[],
        warnings=outcome.built.warnings,
    )


# ---------------------------------------------------------------------------
# Asset catalog
# ---------------------------------------------------------------------------


# The catalog list is bounded: one request can never pull more than this many
# records, so GET /api/assets cannot return an unbounded payload. ``limit`` defaults
# to the ceiling, so a client that sends no paging params still gets every asset a
# tenant has today (well under the cap) in a single call.
_ASSETS_MAX_PAGE = 1000


@router.get("/assets", response_model=AssetList)
def get_assets(
    _: _Auth,
    brand: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_ASSETS_MAX_PAGE)] = _ASSETS_MAX_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AssetList:
    """List catalog assets, newest first, in bounded pages.

    Optional ``brand`` and ``asset`` filters narrow the scan. The optional
    ``status`` filter ("approved" or "needs_review") returns only assets whose
    lifecycle status matches; a never-stamped asset (null status) is excluded by
    any status filter. The records mirror the catalog fields the output manager
    writes, served from Postgres when the source-of-truth flag is on and from the
    on-disk sidecars otherwise.

    ``limit`` (1..1000, default 1000) and ``offset`` (>=0) page the result so the
    response is always bounded; ``total`` and ``has_more`` in the envelope let a
    client walk further pages.
    """
    if status is not None and status not in ("approved", "needs_review"):
        raise HTTPException(
            status_code=_HTTP_422,
            detail="status must be approved or needs_review",
        )
    records = read_assets(brand=brand, asset=asset, status=status)
    total = len(records)
    page = records[offset : offset + limit]
    return AssetList(
        assets=page,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(page) < total,
    )


@router.get("/assets/file")
def get_asset_file(
    request: Request,
    _: _Auth,
    brand: Annotated[str, Query()],
    asset: Annotated[str, Query()],
    file: Annotated[str, Query()],
    kind: Annotated[str, Query()] = "generated",
) -> Response:
    """Serve one asset's bytes, confined to the brand-kits tree.

    Resolves ``brand-kits/<brand>/<kind>/<asset>/<file>`` and refuses anything
    that escapes the kits root, so a crafted path cannot read outside it. Auth is
    the same anonymous-or-required dependency as the rest of the API; serving
    tenant-scoped assets behind real auth will want signed URLs at deploy.

    Conditional GET: the response carries an ``ETag``, and a matching
    ``If-None-Match`` is answered with ``304 Not Modified`` (no body), so a browser
    re-rendering the same thumbnail revalidates cheaply instead of re-downloading.
    """
    if kind not in ("generated", "imported"):
        raise HTTPException(status_code=_HTTP_422, detail="kind must be generated or imported")
    _ensure_brand_visible(brand)
    root = current_kits_dir().resolve()
    target = (root / brand / kind / asset / file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset not found"
        )
    st = target.stat()
    etag = _file_etag(st)
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and _etag_matches(if_none_match, etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": _ASSET_CACHE_CONTROL},
        )
    return _safe_file_response(target, etag=etag, stat_result=st)


@router.delete("/assets/file", response_model=DeleteResult)
def delete_asset_file(
    _: _Owner,
    brand: Annotated[str, Query()],
    asset: Annotated[str, Query()],
    file: Annotated[str, Query()],
    kind: Annotated[str, Query()] = "generated",
) -> DeleteResult:
    """Soft-delete one catalog asset: a recoverable delete that hides it.

    Identifies ``brand-kits/<brand>/<kind>/<asset>/<file>`` exactly as GET
    /api/assets/file does -- the same kind validation, brand-visibility check, and
    path confinement under the kits root -- and refuses anything that escapes the
    tree or does not exist with the same scoped 404. Owner-gated (RLS isolates the
    tenant; this adds the in-workspace authorization for a destructive action).

    The delete is RECOVERABLE: it stamps ``deleted_at`` into the asset's sidecar
    (the durable hide, so the file read path drops it) and best-effort into the
    catalog row (so the DB read path drops it too), but the bytes stay on disk.
    The retention sweeper (``brand_ops.retention_sweep``) hard-purges them only
    after ``BRAND_OPS_RETENTION_DAYS``, so the asset can be recovered until then.
    The act is recorded on the activity feed.
    """
    if kind not in ("generated", "imported"):
        raise HTTPException(status_code=_HTTP_422, detail="kind must be generated or imported")
    _ensure_brand_visible(brand)
    root = current_kits_dir().resolve()
    target = (root / brand / kind / asset / file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset not found"
        )

    # The catalog key the asset row is unique on, and the sidecar beside the file.
    storage_key = f"{brand}/{kind}/{asset}/{file}"
    sidecar = target.with_suffix(target.suffix + ".json")
    # Stamp the delete instant here, at the HTTP boundary, so the retention helper
    # (like the activity feed and the lifecycle transition) reads no clock.
    ts = datetime.now(UTC).isoformat()
    retention.soft_delete_sync(
        get_current_workspace_id(),
        storage_key=storage_key,
        sidecar_path=sidecar,
        deleted_at=ts,
    )

    append_event(
        "you",
        "human",
        "deleted asset",
        brand=brand,
        target=f"{asset}/{file}",
        note=None,
        ts=ts,
    )
    return DeleteResult(
        brand=brand,
        asset_type=asset,
        file=file,
        kind=kind,
        deleted=True,
        deleted_at=ts,
    )


@router.post("/assets/upload", response_model=UploadResult)
async def upload_asset(
    _: _Auth,
    file: Annotated[UploadFile, File()],
    brand: Annotated[str, Form()],
    asset_type: Annotated[str, Form()],
    variant: Annotated[str | None, Form()] = None,
) -> UploadResult:
    """Accept a dropped image into a brand kit's imported/ tree.

    Multipart: an image ``file`` plus form fields ``brand``, ``asset_type``, and
    an optional ``variant``. The brand must exist and the asset type must be
    known; the file must be an accepted image type within the size cap. The bytes
    land at ``brand-kits/<brand>/imported/<asset_type>/<name>`` with a catalog
    sidecar (source="upload"), and the new record comes back in the same shape
    GET /api/assets serves, so the Library can append it without a reload.

    Every bad input is a clean 422, not a 500: an unknown brand is 404, an
    oversized or unsupported file is 422 with a stable error code.
    """
    # Brand existence is checked here (loading a brand is the route's concern);
    # the file mechanics live in upload.save_upload.
    try:
        load_brand(brand)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None

    data = await file.read()
    # Guard the size before we hand the bytes on, so an oversized upload is a
    # clean 422 rather than work the helper has to redo.
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=_HTTP_422,
            detail={
                "error": "file_too_large",
                "detail": f"file is larger than the {mb} MB limit",
            },
        )

    try:
        record = save_upload(
            brand=brand,
            asset_type=asset_type,
            filename=file.filename or "",
            content_type=file.content_type or "",
            data=data,
            variant=variant,
        )
    except UploadError as exc:
        raise HTTPException(
            status_code=_HTTP_422,
            detail={"error": exc.code, "detail": exc.message},
        ) from None

    return UploadResult(asset=record)


def _apply_transition(
    *,
    brand: str,
    file: str,
    asset: str,
    kind: str,
    to: str,
    note: str | None,
    actor: str,
) -> AssetRecord:
    """Drive a lifecycle transition and return the refreshed catalog record.

    The shared boundary both POST /assets/transition and POST /assets/approve
    use, so the two can never drift. Validates ``kind``, stamps the activity
    ``ts`` here (the HTTP boundary owns the wall clock), delegates the sidecar
    write and the feed event to ``compliance.transition``, and maps its domain
    errors onto HTTP: an unknown ``kind`` or a bad ``to`` is a 422, a missing
    sidecar a 404, an unreadable/non-record sidecar a 422.
    """
    _ensure_brand_visible(brand)
    if kind not in ("generated", "imported"):
        raise HTTPException(
            status_code=_HTTP_422, detail="kind must be generated or imported"
        )
    # Stamp the activity timestamp here, at the HTTP boundary, so the domain
    # helper stays clock-free and reproducible.
    ts = datetime.now(UTC).isoformat()
    try:
        data = transition(
            brand,
            asset,
            file,
            kind,
            to,
            note=note,
            actor=actor,
            ts=ts,
        )
    except ValueError as exc:
        # A bad target state (or an unreadable/non-record sidecar) is unprocessable.
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset sidecar not found"
        ) from None

    # Rebuild the catalog record from the refreshed sidecar, resolving the same
    # image path the catalog and approve flow resolve. ``transition`` above already
    # confined and would have 404'd an escaping path; re-confine here as defense in
    # depth (and because this path feeds a served URL).
    image_path = safe_subpath(current_kits_dir(), brand, kind, asset, file)
    if image_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset sidecar not found"
        )
    sidecar = image_path.with_suffix(image_path.suffix + ".json")
    record = _coerce_record(data, sidecar, image_path, kind=kind)
    if record is None:  # pragma: no cover - transition just wrote a valid record
        raise HTTPException(
            status_code=_HTTP_422, detail="sidecar is not an asset record"
        )
    return record


@router.post("/assets/transition", response_model=TransitionResult)
def transition_asset(body: TransitionRequest, _: _Owner) -> TransitionResult:
    """Move an asset to a new lifecycle state and record it on the activity feed.

    Locates ``brand-kits/<brand>/<kind>/<asset>/<file>`` (and its sidecar) inside
    the kits tree and read-modify-writes ``lifecycle_status=to`` into it,
    preserving every other key, then appends one activity event attributing the
    move to ``actor`` (default ``you``) with an optional ``note``. ``to`` is one
    of needs_review, approved, changes_requested, archived. The refreshed record
    (the same AssetRecord shape GET /api/assets serves) comes back under
    ``asset``.

    A missing sidecar is a 404; an unknown ``kind`` or an invalid ``to`` is a
    422, consistent with the rest of the asset API.
    """
    record = _apply_transition(
        brand=body.brand,
        file=body.file,
        asset=body.asset,
        kind=body.kind,
        to=body.to,
        note=body.note,
        actor=body.actor,
    )
    return TransitionResult(asset=record)


@router.post("/assets/approve", response_model=ApproveResult)
def approve_asset(body: ApproveRequest, _: _Owner) -> ApproveResult:
    """Operator override: flip an asset's lifecycle status to ``approved``.

    A thin alias for POST /api/assets/transition with ``to="approved"`` and
    ``actor="you"`` — the human accepting an asset the deterministic checker
    flagged needs_review. It writes ``lifecycle_status="approved"`` into the
    sidecar (preserving every other key) and appends an "approved" activity
    event, returning the refreshed catalog record under ``asset``.

    A path with no sidecar is a 404; an unknown kind is a 422, consistent with
    the rest of the asset API.
    """
    record = _apply_transition(
        brand=body.brand,
        file=body.file,
        asset=body.asset,
        kind=body.kind,
        to="approved",
        note=None,
        actor="you",
    )
    return ApproveResult(asset=record)


# ---------------------------------------------------------------------------
# Activity / provenance feed
# ---------------------------------------------------------------------------


@router.get("/activity", response_model=ActivityList)
def get_activity(
    _: _Auth,
    brand: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ActivityList:
    """Read the append-only activity / provenance feed, newest first.

    Returns the recent events from the on-disk feed, optionally filtered to one
    ``brand`` slug, capped at ``limit`` (1..500, default 50). Each event records
    who did what, when, and to which asset — the orientation surface for both the
    human and the agent paths. A corrupt line in the log is skipped, never fatal,
    and a missing log is simply an empty feed. A workspace-scoped caller sees only
    its own brands' events (a brand-less rollup event is dropped for them).
    """
    allowed = get_allowed_brands()
    if allowed is None:
        # Unscoped (CLI / stdio MCP / tests): the original global feed.
        raw = read_events(brand=brand, limit=limit)
    elif brand is not None:
        # Scoped: a brand outside the workspace discloses nothing.
        raw = read_events(brand=brand, limit=limit) if brand in allowed else []
    else:
        # Scoped, no brand filter: read a generous window, keep only this
        # workspace's brands' events (a brand-less event has no tenant
        # attribution, so drop it), newest first, capped at limit.
        raw = [
            e for e in read_events(brand=None, limit=500) if e.get("brand") in allowed
        ][:limit]
    events = [ActivityEvent.model_validate(e) for e in raw]
    return ActivityList(events=events)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents", response_model=AgentList)
def get_agents(_: _Auth) -> AgentList:
    """List the in-module agents the GUI can run, projected from the registry.

    Each entry carries what a UI needs to render and gate an agent: its name,
    description, JSON-schema input contract, the auth scopes a caller must hold,
    and the minimum suite tier. This is the same registry that backs the MCP
    server and the Brigade manifest, so the three never disagree on the agent
    set.
    """
    agents = [
        AgentInfo(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema,
            scopes_required=list(spec.scopes_required),
            tier_required=spec.tier_required,
        )
        for spec in AGENT_REGISTRY
    ]
    return AgentList(agents=agents)


@router.post("/agents/{name}/run", response_model=AgentRunResult)
async def run_agent_route(
    name: str,
    payload: Annotated[dict[str, Any], Body(default_factory=dict)],
    principal: _Auth,
) -> AgentRunResult:
    """Run one agent against an arbitrary JSON payload and return its result.

    The body is the agent's payload verbatim (its shape is per-agent; see the
    input_schema from GET /api/agents). An unknown agent name is a 404. The
    agents degrade gracefully when the LLM gateway or Brigade is absent: they
    return a result whose ``status`` is something like ``skipped`` or
    ``research_only`` rather than raising, and that is surfaced as a normal 200.
    A genuine exception from the agent's run becomes a 502 carrying the message,
    never a bare 500.
    """
    # Least-privilege delegation gate: a delegated agent token (Brigade act-token)
    # must hold the agent's required scopes. A no-op for human callers and the
    # unscoped CLI, and a no-op entirely until BRAND_OPS_ENFORCE_AGENT_SCOPES is on
    # (the suite Brigade issuer does not mint brandops:* scopes yet).
    spec = next((s for s in AGENT_REGISTRY if s.name == name), None)
    if spec is not None and not agent_scopes_satisfied(principal, spec.scopes_required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"agent {name!r} requires scope(s): {', '.join(spec.scopes_required)}",
        )
    try:
        # Thread metering context: when the caller is workspace-scoped, the
        # agent records best-effort spend against specialist/<name>. Many agent
        # payloads carry a "brand" slug; pass it through for spend attribution
        # (best-effort, only when it is a non-empty string). Never affects the run.
        payload_brand = payload.get("brand") if isinstance(payload, dict) else None
        result = await run_agent(
            name,
            payload,
            workspace_id=principal.workspace_id,
            brand_slug=payload_brand if isinstance(payload_brand, str) and payload_brand else None,
        )
    except UnknownAgent as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - any agent failure is a clean 502, not a 500
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"agent error: {exc}"
        ) from None

    # Agents stamp a status on their result; default to "ok" if one ever omits
    # it. The full result is passed through verbatim under ``result``.
    agent_status = result.get("status", "ok") if isinstance(result, dict) else "ok"
    return AgentRunResult(
        agent=name,
        status=str(agent_status),
        result=result if isinstance(result, dict) else {"value": result},
    )


# ---------------------------------------------------------------------------
# Generation planner: free text -> a valid GenerateRequest
# ---------------------------------------------------------------------------


async def run_plan_generation(
    *,
    text: str,
    brand: str | None = None,
    asset: str | None = None,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Plan a free-text generation ask into a valid GenerateRequest dict.

    The shared core of POST /plan-generation, callable from the MCP server so the
    agent path and the HTTP path run the exact same planner over the exact same
    brand context with the same workspace scoping. Returns the planner result
    dict ({request, rationale, assumed}). Raises ``ValueError`` when no brand or
    asset type could be resolved (the operator has no brands), which the HTTP
    route maps to a 422 and the MCP tool maps to a structured error; never raises
    for a normal LLM problem (the planner degrades to a deterministic heuristic).
    """
    brands = _brands_context()
    planner = GenerationPlanner(
        workspace_id=workspace_id,
        agent_kind="specialist",
        agent_ref="generation_planner",
        brand_slug=brand,
    )
    result = await planner.plan(
        text,
        brands,
        brand=brand,
        asset=asset,
        variant=variant,
        topic=topic,
        platform=platform,
        backend=backend,
    )
    request = result.get("request") or {}
    if not request.get("brand") or not request.get("asset"):
        # No brand or no asset could be resolved (the operator has no brands, or no
        # asset types are registered). There is nothing valid to plan.
        raise ValueError(
            "cannot plan a generation: no brand or asset type is available"
        )
    return result


@router.post("/plan-generation", response_model=PlanGenerationReply)
async def plan_generation(body: PlanGenerationRequest, principal: _Auth) -> PlanGenerationReply:
    """Interpret a free-text generation ask into a concrete, valid GenerateRequest.

    The Studio's free-text box ("make an OG image for the investor update, dark")
    posts here with the current control values as context. The planner asks the
    local model (through the shared metered gateway) to pick a brand, asset type,
    variant, topic, and platform from the real brand slugs and asset types the
    backend knows, validates that pick, and returns a fully valid GenerateRequest
    plus a short rationale and the fields it assumed. The frontend feeds
    ``request`` straight into the existing prompt-preview/generate flow.

    This route never 500s on a normal request and never returns an invalid
    GenerateRequest. When the gateway is unavailable, the model's reply will not
    parse, or its pick fails validation, the planner falls back to a deterministic
    server-side keyword heuristic, so the endpoint always answers 200 with a
    sensible plan. An operator with no brands at all is the one 422 (there is
    nothing to generate for).
    """
    try:
        result = await run_plan_generation(
            text=body.text,
            brand=body.brand,
            asset=body.asset,
            variant=body.variant,
            topic=body.topic,
            platform=body.platform,
            backend=body.backend,
            workspace_id=principal.workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    return PlanGenerationReply.model_validate(result)


# ---------------------------------------------------------------------------
# Director (Iris): the conversational orchestrator over Studio
# ---------------------------------------------------------------------------


def _brands_context() -> list[dict[str, str]]:
    """The operator's brands as {slug, display_name}, for Iris to know by name.

    Reuses the same loader the brand list endpoint uses, so Iris and the GUI see
    the same brand set. A brand that fails validation is skipped rather than
    breaking the chat, exactly as it is skipped from the brand list.
    """
    context: list[dict[str, str]] = []
    for slug in list_brands():
        try:
            brand = load_brand(slug)
        except (BrandNotFound, ValueError):
            continue
        context.append(
            {"slug": brand.brand.slug, "display_name": brand.brand.display_name}
        )
    return context


async def run_director_chat(
    *,
    message: str,
    brand: str | None = None,
    history: list[dict[str, str]] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run one Director (Iris) turn and return her structured reply dict.

    The shared core of POST /director/chat, callable from the MCP server so the
    agent path and the HTTP path run the exact same Director flow: build the
    payload with the operator's brand context, run the agent (degrading to a
    graceful in-persona fallback on any LLM problem), fulfill an ``agent`` or
    ``compliance`` intent through the same helpers the route uses, and emit the
    provenance event. ``history`` is a list of {role, text} turns, oldest first.
    Returns the DirectorReply-shaped dict; never raises.
    """
    turns = [
        {"role": str(t.get("role", "")), "text": str(t.get("text", ""))}
        for t in (history or [])
    ]
    payload = {
        "message": message,
        "brand": brand,
        "history": turns,
        "brands_context": _brands_context(),
    }
    try:
        # Thread metering context: when the caller is workspace-scoped, the
        # Director's LLM turn records a best-effort spend event against
        # director/iris. agent_ref is "iris" (the persona), not the registry name
        # "director", so it matches the synthesized governance row the Crew page
        # shows. Metering never affects the run (see Agent.chat).
        director = DirectorAgent(
            workspace_id=workspace_id,
            agent_kind="director",
            agent_ref="iris",
            brand_slug=brand,
        )
        result = await director.run(payload)
    except Exception:  # noqa: BLE001 - an LLM/agent problem degrades to a fallback, never raises
        result = {
            "reply": (
                "I hit a snag reaching my planner. Tell me the brand and what "
                "you need and I will line it up."
            ),
            "intent": "chat",
            "brand": brand,
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
        }

    # When Iris classified the turn as creating an agent, act on it here: stand
    # up (or surface) the on-brand identity through agent_identity and rewrite
    # the reply in her voice. Any failure degrades to a normal chat reply inside
    # the helper, so this still returns a clean reply.
    if result.get("intent") == "agent":
        result = _fulfill_agent_intent(result)
    # When Iris classified the turn as a compliance CHECK, run the deterministic
    # gate here and rewrite the reply with the verdict. Degrades to chat inside
    # the helper. The catalog rollup is blocking (file reads, and an image decode
    # for any asset whose verdict is not already stamped), so it runs in a worker
    # thread to keep the request loop free for concurrent turns in this worker.
    elif result.get("intent") == "compliance":
        result = await asyncio.to_thread(_fulfill_compliance_intent, result)

    # Record what Iris did on the provenance feed. Best-effort: a feed failure
    # never changes the reply.
    _emit_director_event(result)
    return result


@router.post("/director/chat", response_model=DirectorReply)
async def director_chat(body: DirectorChatRequest, principal: _Auth) -> DirectorReply:
    """Talk to Iris, the brand director, and get a reply plus a routed intent.

    Iris is the single conversational agent for the Studio. She answers in
    persona and classifies the request into an ``intent`` the frontend acts on:
    ``single`` drives the prompt-preview/generate flow, ``pack`` runs the
    asset_pack agent, ``research`` runs brand_research, and ``chat`` is talk
    only. The operator's brands (slug + display name) and the active brand are
    passed to her so she works on brand.

    This route never 500s on an LLM problem. When the gateway is unavailable or
    the model's reply will not parse, the agent returns a graceful in-persona
    fallback with intent ``chat`` and the route answers a normal 200. Any
    unexpected error is caught and degraded the same way, so the Studio's chat
    is always live.
    """
    history = [{"role": turn.role, "text": turn.text} for turn in body.history]
    result = await run_director_chat(
        message=body.message,
        brand=body.brand,
        history=history,
        workspace_id=principal.workspace_id,
    )
    return DirectorReply.model_validate(result)


def _fulfill_agent_intent(result: dict[str, Any]) -> dict[str, Any]:
    """Create (or surface) the agent identity Iris classified, in her voice.

    Called only when ``result['intent'] == 'agent'``. The agent's normalizer put
    the extracted ``agent_name``/``agent_role`` on ``result`` and resolved
    ``brand`` to the active brand. This calls ``agent_identity.create_agent``; on
    a ``FileExistsError`` it loads and surfaces the existing agent instead of
    erroring. On success or a duplicate it rewrites ``reply`` as Iris narrating,
    first person, and offering to generate the avatar, and sets ``agent`` to the
    {slug, name, role, brand} the frontend renders.

    It never raises: a missing brand or name, an unknown brand, or any creation
    error degrades the turn to a normal in-persona chat reply (intent ``chat``)
    so the route always answers 200. The ``agent_name``/``agent_role`` helper
    keys are dropped before return; the wire model ignores them anyway.
    """
    brand = result.get("brand")
    name = result.get("agent_name")
    role = result.get("agent_role") or "brand steward"

    # No brand or no name is not actionable as a creation. Keep Iris's reply but
    # downgrade to chat (and nudge for the missing piece if her words did not).
    if not (isinstance(brand, str) and brand) or not (isinstance(name, str) and name):
        return _agent_intent_to_chat(
            result,
            fallback_reply=(
                "I can set up an agent once we are in a brand. Open the brand you "
                "want it on and tell me its name and what it does."
            ),
        )

    created = True
    try:
        ident = agent_identity.create_agent(brand, name, role)
    except FileExistsError:
        # The agent already exists: surface the one on disk rather than error.
        created = False
        try:
            ident = agent_identity.load_agent(brand, agent_identity.slugify(name))
        except Exception:  # noqa: BLE001 - cannot reload -> degrade to chat, never 500
            return _agent_intent_to_chat(result)
    except Exception:  # noqa: BLE001 - unknown brand, bad name/role, or any failure -> chat, never 500
        # BrandNotFound and agent_identity.AgentIdentityError both land here, as
        # does anything unexpected. The Studio chat is never dead.
        return _agent_intent_to_chat(result)

    agent_slug = ident["slug"]
    agent_name = ident.get("name") or name
    agent_role = ident.get("role") or role
    if created:
        reply = (
            f"Done. I created {agent_name}, your {agent_role}, on the brand. "
            f"They inherit the brand voice and palette. Want me to generate "
            f"{agent_name}'s avatar now?"
        )
    else:
        reply = (
            f"{agent_name} is already on the team as your {agent_role}. I left "
            f"them as they are. Want me to generate {agent_name}'s avatar?"
        )

    out = dict(result)
    out["intent"] = "agent"
    out["reply"] = reply
    out["brand"] = brand
    out["agent"] = {
        "slug": agent_slug,
        "name": agent_name,
        "role": agent_role,
        "brand": brand,
    }
    out.pop("agent_name", None)
    out.pop("agent_role", None)
    return out


def _agent_intent_to_chat(
    result: dict[str, Any], *, fallback_reply: str | None = None
) -> dict[str, Any]:
    """Degrade an agent-intent turn to a clean chat reply (never 500).

    Keeps Iris's own reply when she wrote one; otherwise uses ``fallback_reply``
    or a fixed in-persona line. Clears every scope field and the agent helper
    keys so the result is a consistent chat ``DirectorReply``.
    """
    reply = result.get("reply")
    reply = reply if isinstance(reply, str) and reply.strip() else None
    return {
        "reply": reply
        or fallback_reply
        or (
            "I could not set that agent up just now. Tell me the brand and the "
            "agent's name and role and I will line it up."
        ),
        "intent": "chat",
        "brand": result.get("brand"),
        "asset": None,
        "variant": None,
        "pack": None,
        "topic": None,
        "agent": None,
    }


def _fulfill_compliance_intent(result: dict[str, Any]) -> dict[str, Any]:
    """Run the deterministic compliance gate Iris routed to, and narrate it.

    Called only when ``result['intent'] == 'compliance'`` — the operator asked to
    CHECK existing work, not make new work. If the normalizer kept ``topic`` (the
    operator pasted copy) that copy is scored as text; otherwise the brand's whole
    catalog is scored with the same engine GET /brands/{slug}/compliance uses. The
    verdict is attached as ``compliance`` and Iris's reply is rewritten to state
    it in her voice.

    Never raises: no brand, an unknown brand, or any engine error degrades the
    turn to a normal in-persona chat reply (intent ``chat``) so the route always
    answers 200.
    """
    brand = result.get("brand")
    if not (isinstance(brand, str) and brand):
        return _agent_intent_to_chat(
            result,
            fallback_reply=(
                "Tell me which brand to check and I will run it against that "
                "brand's rules. Open the brand or name it."
            ),
        )
    try:
        loaded = _load_brand_or_404(brand)
        rules = build_rules(loaded)
        topic = result.get("topic")
        if isinstance(topic, str) and topic.strip():
            verdict = check_text_target(brand, topic.strip(), rules)
            scope = "copy"
        else:
            counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
            scores: list[int] = []
            for record in read_assets(brand=brand):
                checked_status, checked_score, _ = _asset_verdict(
                    brand, record, rules
                )
                counts[checked_status] = counts.get(checked_status, 0) + 1
                if checked_status != "skipped":
                    scores.append(checked_score)
            avg = round(sum(scores) / len(scores)) if scores else 0
            status = "fail" if counts["fail"] else ("warn" if counts["warn"] else "pass")
            verdict = {
                "brand": brand,
                "target": "catalog",
                "status": status,
                "score": avg,
                "checks": [],
                "violations": [],
                "counts": counts,
            }
            scope = "catalog"
    except HTTPException:
        # _load_brand_or_404 raised (unknown brand). Degrade to chat, never 500.
        return _agent_intent_to_chat(
            result,
            fallback_reply=(
                "I could not find that brand to check. Tell me which brand and I "
                "will run it against its rules."
            ),
        )
    except Exception:  # noqa: BLE001 - any engine problem -> chat, never 500
        return _agent_intent_to_chat(result)

    out = dict(result)
    out["intent"] = "compliance"
    out["reply"] = _narrate_compliance(scope, verdict)
    out["compliance"] = verdict
    out.pop("agent_name", None)
    out.pop("agent_role", None)
    return out


def _narrate_compliance(scope: str, verdict: dict[str, Any]) -> str:
    """Iris stating a compliance verdict in her voice: first person, no em-dash."""
    status = str(verdict.get("status", "skipped"))
    score = verdict.get("score", 0)
    if scope == "catalog":
        counts = verdict.get("counts") or {}
        passed = counts.get("pass", 0)
        warn = counts.get("warn", 0)
        fail = counts.get("fail", 0)
        head = (
            f"I ran the catalog against the brand rules. {passed} pass, {warn} "
            f"warn, {fail} fail, average {score}."
        )
        if fail:
            return head + " The fails are off brand and need a fix before they ship."
        if warn:
            return head + " The warnings are close but worth a look."
        return head + " It is clean."
    verb = {
        "pass": "is on brand",
        "warn": "is close but not clean",
        "fail": "is off brand",
    }.get(status, "could not be scored")
    line = f"I checked that copy against the brand rules. It {verb}, scoring {score}."
    violations = verdict.get("violations") or []
    if violations:
        first = violations[0]
        message = first.get("message") if isinstance(first, dict) else str(first)
        if message:
            line += f" The main issue: {message}"
    return line


# The human-readable verb the activity feed shows for each director intent. Iris
# either routed the turn to work (single/pack/research/compliance) or just talked.
_DIRECTOR_ACTION_FOR_INTENT = {
    "chat": "chatted",
    "single": "routed a single asset",
    "pack": "routed a pack",
    "research": "routed research",
    "agent": "created agent",
    "compliance": "ran a compliance check",
}


def _emit_director_event(result: dict[str, Any]) -> None:
    """Append one activity event for a director turn. Best-effort, never raises."""
    try:
        from ..activity import append_event

        intent = str(result.get("intent", "chat"))
        brand = result.get("brand")
        # The target names the thing Iris pointed at, when she pointed at one.
        target = None
        if intent == "single" and result.get("asset"):
            target = str(result["asset"])
        elif intent == "pack" and result.get("pack"):
            target = f"pack/{result['pack']}"
        elif intent == "agent" and isinstance(result.get("agent"), dict):
            target = str(result["agent"].get("slug") or "")
        elif intent == "compliance" and isinstance(result.get("compliance"), dict):
            verdict = result["compliance"]
            target = f"{verdict.get('target', 'catalog')}/{verdict.get('status', '')}"
        append_event(
            "iris",
            "agent",
            _DIRECTOR_ACTION_FOR_INTENT.get(intent, "chatted"),
            brand=str(brand) if isinstance(brand, str) and brand else None,
            target=target,
            note=result.get("topic") if isinstance(result.get("topic"), str) else None,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the chat
        pass


# ---------------------------------------------------------------------------
# Agent identities: on-brand avatar + persona + manifest for a brand's agents.
# A company spinning up agents gets identities that are on-brand by construction.
# ---------------------------------------------------------------------------


@router.get("/brands/{slug}/agent-team", response_model=AgentTeam)
def get_brand_agent_team(slug: str, _: _Auth) -> AgentTeam:
    """The brand's full agent team plus a brand-governance rollup.

    One read for the team view: the shared Director persona (Iris), this brand's
    Steward (named for the brand, its avatar the brand's canonical mark), every
    created agent identity (the same shape GET /api/brands/{slug}/agents serves),
    and a ``governance`` tally of those agents by avatar compliance (on_brand /
    needs_review / unrated). An unknown brand is a 404.
    """
    try:
        team = agent_identity.build_team(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return AgentTeam.model_validate(team)


@router.get("/brands/{slug}/agents", response_model=AgentIdentityList)
def list_brand_agents(slug: str, _: _Auth) -> AgentIdentityList:
    """Every agent identity owned by the brand."""
    try:
        agents = agent_identity.list_agents(slug)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    return AgentIdentityList(agents=[AgentIdentityOut(**a) for a in agents])


@router.post(
    "/brands/{slug}/agents",
    response_model=AgentIdentityOut,
    status_code=status.HTTP_201_CREATED,
)
def create_brand_agent(slug: str, body: CreateAgentIdentityRequest, _: _Owner) -> AgentIdentityOut:
    """Create an on-brand agent identity (inherits the brand voice tone)."""
    try:
        ident = agent_identity.create_agent(slug, body.name, body.role, body.bio)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    try:
        append_event(
            "iris", "agent", "created agent", brand=slug, target=ident["slug"],
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - advisory feed
        pass
    return AgentIdentityOut(**ident)


@router.post("/brands/{slug}/agents/{agent}/avatar", response_model=AgentIdentityOut)
def generate_brand_agent_avatar(slug: str, agent: str, _: _Owner) -> AgentIdentityOut:
    """Generate the agent's on-brand avatar through the resolved image backend."""
    try:
        ident = agent_identity.generate_avatar(slug, agent)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except BackendNotConfigured as exc:
        raise HTTPException(
            status_code=_HTTP_422,
            detail={"error": "backend_not_configured", "backend": "image", "detail": str(exc)},
        ) from None
    except QuotaExceeded as exc:
        # Over the workspace image spend cap (or an agent weekly budget). The
        # request was valid; the tenant is rate/spend limited, so 429.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from None
    except BackendError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    try:
        append_event(
            "iris", "agent", "generated agent avatar", brand=slug, target=ident["slug"],
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - advisory feed
        pass
    return AgentIdentityOut(**ident)


def _serve_avatar(path: Path | None, size: int | None) -> Response:
    """Serve a confined avatar file, optionally downscaled to a square ``size``.

    The shared body of the avatar and variant GET routes. An out-of-range
    ``size`` is a 422 up front (input validation, independent of the resource).
    ``path`` is the file the caller resolved (or None when the agent has no
    avatar): a missing file is a 404. With no ``size`` the full image is streamed
    as today (FileResponse). With a valid ``size`` the source PNG is downscaled in
    memory (LANCZOS, alpha-preserving) and returned as image/png bytes. The resize
    is best-effort: if the source can't be opened the route falls back to serving
    the file rather than 500ing.
    """
    if size is not None and size not in agent_identity.AVATAR_SIZES:
        raise HTTPException(
            status_code=_HTTP_422,
            detail=f"size must be one of {list(agent_identity.AVATAR_SIZES)}",
        )
    root = current_kits_dir().resolve()
    if path is None or not path.resolve().is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="avatar not generated")
    if size is None:
        return _safe_file_response(path)
    try:
        data = agent_identity.resize_avatar_png(path, size)
    except agent_identity.AgentIdentityError as exc:
        # An unsupported size slipped past the guard: still a 422, never a 500.
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    except Exception:  # noqa: BLE001 - a bad source image degrades to the full file, never a 500
        return _safe_file_response(path)
    # The resized derivative is always raster PNG, so it stays inline for <img>;
    # nosniff keeps a browser from re-sniffing the bytes into another type.
    return Response(
        content=data,
        media_type="image/png",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/brands/{slug}/agents/{agent}/avatar")
def get_brand_agent_avatar(
    slug: str,
    agent: str,
    _: _Auth,
    size: Annotated[int | None, Query()] = None,
) -> Response:
    """Serve the agent's avatar bytes, confined to the kits tree.

    With no ``size`` the full avatar is served. ``?size=`` (one of 32, 64, 128,
    256, 512) returns that square px derivative as image/png, downscaled on the
    fly; an unsupported size is a 422.
    """
    _ensure_brand_visible(slug)
    try:
        path = agent_identity.avatar_path(slug, agent)
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    return _serve_avatar(path, size)


@router.post("/brands/{slug}/agents/{agent}/variants", response_model=AgentIdentityOut)
def generate_brand_agent_variants(slug: str, agent: str, _: _Owner) -> AgentIdentityOut:
    """Generate the agent's full on-brand avatar state set (primary + variants).

    Produces an avatar for every state — primary (avatar.png, generated here if
    missing), thinking, success, alert — through the resolved image backend, each
    on-brand and compliance-checked, and records the ``variants`` map on the
    agent. Returns the refreshed identity carrying the ``variants`` URL map.
    Degrades like the avatar route: a missing brand or agent is a 404, a backend
    with no credentials is a 422, and a real provider failure is a 502.
    """
    try:
        ident = agent_identity.generate_variants(slug, agent)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except BackendNotConfigured as exc:
        raise HTTPException(
            status_code=_HTTP_422,
            detail={"error": "backend_not_configured", "backend": "image", "detail": str(exc)},
        ) from None
    except QuotaExceeded as exc:
        # Over the workspace image spend cap (or an agent weekly budget). The
        # request was valid; the tenant is rate/spend limited, so 429.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from None
    except BackendError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None
    try:
        append_event(
            "iris", "agent", "generated agent avatar variants", brand=slug,
            target=ident["slug"], ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - advisory feed
        pass
    return AgentIdentityOut(**ident)


@router.get("/brands/{slug}/agents/{agent}/variants/{state}")
def get_brand_agent_variant(
    slug: str,
    agent: str,
    state: str,
    _: _Auth,
    size: Annotated[int | None, Query()] = None,
) -> Response:
    """Serve one avatar state's bytes, confined to the kits tree.

    An unknown state or a state that has not been generated is a 404, the same as
    the avatar route. The path is resolved and confined under the kits root so a
    crafted slug or state can never read outside it. ``?size=`` (one of 32, 64,
    128, 256, 512) returns that square px derivative as image/png; an unsupported
    size is a 422.
    """
    _ensure_brand_visible(slug)
    try:
        path = agent_identity.variant_path(slug, agent, state)
    except agent_identity.AgentIdentityError:
        # A bad slug or an unknown state: nothing to serve, treat as not found.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="variant not found"
        ) from None
    return _serve_avatar(path, size)


@router.get("/brands/{slug}/agents/{agent}/manifest")
def get_brand_agent_manifest(slug: str, agent: str, _: _Auth) -> dict:
    """The Brigade/suite-consumable identity manifest for the agent."""
    try:
        return agent_identity.build_manifest(slug, agent)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None


@router.patch("/brands/{slug}/agents/{agent}", response_model=AgentIdentityOut)
def update_brand_agent(
    slug: str, agent: str, body: UpdateAgentRequest, _: _Owner
) -> AgentIdentityOut:
    """Edit an agent's persona text (role and/or bio); avatar and voice untouched."""
    if body.role is not None and not body.role.strip():
        raise HTTPException(status_code=_HTTP_422, detail="agent role cannot be empty")
    try:
        ident = agent_identity.update_agent(slug, agent, role=body.role, bio=body.bio)
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    try:
        append_event(
            "iris", "agent", "updated agent", brand=slug, target=ident["slug"],
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - advisory feed
        pass
    return AgentIdentityOut(**ident)


@router.put("/brands/{slug}/agents/{agent}/voice", response_model=AgentIdentityOut)
def set_brand_agent_voice(
    slug: str, agent: str, body: SetAgentVoiceRequest, _: _Owner
) -> AgentIdentityOut:
    """Bind (or clear) the agent's spoken voice from the catalog.

    A null/empty ``voice_id`` clears the binding; a non-empty one must be a catalog
    voice (else 422). The engine is resolved from the catalog when omitted.
    """
    voice_id = (body.voice_id or "").strip() or None
    if voice_id is not None and not voices.is_known(voice_id):
        raise HTTPException(status_code=_HTTP_422, detail=f"unknown voice {voice_id!r}")
    try:
        ident = agent_identity.set_agent_voice(
            slug, agent, voice_id, engine=body.engine, speed=body.speed
        )
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    try:
        append_event(
            "iris", "agent",
            "set agent voice" if voice_id else "cleared agent voice",
            brand=slug, target=ident["slug"], ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - advisory feed
        pass
    return AgentIdentityOut(**ident)


@router.get("/voices", response_model=VoiceCatalog)
def list_voice_catalog(_: _Auth) -> VoiceCatalog:
    """The catalog of assignable agent voices, and whether live preview is wired."""
    return VoiceCatalog(
        voices=[VoiceOption(**v) for v in voices.list_voices()],
        preview_available=voices.preview_available(),
    )


@router.post("/voices/preview")
def preview_voice(body: VoicePreviewRequest, _: _Auth) -> Response:
    """Synthesize one short sample utterance in a voice, via the configured orator.

    Best-effort: returns audio bytes when orator is wired and reachable, a 422 for
    an unknown voice, and a 503 when preview is not configured or orator is down,
    so the GUI can hide the control rather than surface a 500. The proxy only ever
    targets the one configured orator (no caller-supplied host).
    """
    if not voices.is_known(body.voice_id):
        raise HTTPException(
            status_code=_HTTP_422, detail=f"unknown voice {body.voice_id!r}"
        )
    try:
        audio, content_type = voices.synthesize_preview(
            body.voice_id, text=body.text, speed=body.speed
        )
    except voices.VoicePreviewError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from None
    return Response(
        content=audio,
        media_type=content_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Agent governance + spend: the Crew page's real controls and metered spend.
# Each agent has an OPERATING CONTRACT in a workspace (autonomy posture, standing
# orders, an optional weekly budget, a model override) persisted in
# agent_governance, plus real 7-day spend rolled up from agent_spend_event. The
# GET synthesizes default rows for the well-known agents so the page always
# renders; the PUT upserts one agent's contract.
# ---------------------------------------------------------------------------

# How many days of spend the Crew page rolls up per agent.
_SPEND_WINDOW_DAYS = 7


def _spend_key(agent_kind: str, agent_ref: str) -> tuple[str, str]:
    return (agent_kind, agent_ref)


def _governance_out(
    *,
    agent_kind: str,
    agent_ref: str,
    row: Any | None,
    spend: dict[tuple[str, str], dict[str, Any]],
) -> AgentGovernanceOut:
    """Project a saved row (or its synthesized default) plus 7-day spend to wire.

    ``row`` is the saved ``AgentGovernance`` ORM object, or None when the agent
    has never been configured -- in which case the honest defaults are used
    (autonomy 'propose', the seed standing orders, no budget, the agent's own
    model) and ``is_default`` is True. ``spend`` maps (kind, ref) to the rolled
    totals; a missing key means no spend in the window.
    """
    totals = spend.get(_spend_key(agent_kind, agent_ref))
    spend_cents = float(totals["cost_cents"]) if totals else 0.0
    tokens = int(totals["total_tokens"]) if totals else 0
    calls = int(totals["calls"]) if totals else 0
    if row is None:
        return AgentGovernanceOut(
            agent_kind=agent_kind,
            agent_ref=agent_ref,
            autonomy=governance.DEFAULT_AUTONOMY,
            standing_orders=governance.default_standing_orders(agent_kind, agent_ref),
            weekly_budget_cents=None,
            model=None,
            spend_cents_7d=round(spend_cents, 4),
            tokens_7d=tokens,
            calls_7d=calls,
            is_default=True,
        )
    return AgentGovernanceOut(
        agent_kind=agent_kind,
        agent_ref=agent_ref,
        autonomy=row.autonomy or governance.DEFAULT_AUTONOMY,
        standing_orders=(
            row.standing_orders
            if row.standing_orders is not None
            else governance.default_standing_orders(agent_kind, agent_ref)
        ),
        weekly_budget_cents=row.weekly_budget_cents,
        model=row.model,
        spend_cents_7d=round(spend_cents, 4),
        tokens_7d=tokens,
        calls_7d=calls,
        is_default=False,
    )


def _well_known_agents(brand_slugs: list[str]) -> list[tuple[str, str]]:
    """The (kind, ref) pairs the Crew page always shows, even with no saved row.

    The Director (director/iris), one Steward per brand the workspace owns
    (steward/<slug>), and every shared specialist from the agent registry
    (specialist/<name>). The page renders a default-shaped card for any of these
    that has no saved governance row yet.
    """
    pairs: list[tuple[str, str]] = [("director", "iris")]
    pairs.extend(("steward", slug) for slug in brand_slugs)
    pairs.extend(("specialist", spec.name) for spec in AGENT_REGISTRY)
    return pairs


def _merge_governance_with_spend(
    *,
    rows: list[Any],
    spend_rows: list[Any],
    brand_slugs: list[str],
) -> list[AgentGovernanceOut]:
    """Merge saved governance rows, synthesized defaults, and 7-day spend.

    Every saved row is emitted with its spend joined; additionally a default
    (is_default=True) row is synthesized for each well-known agent that has no
    saved row, so the page always renders the Director, every brand's Steward,
    and every specialist. The result is ordered: Director, Stewards, Specialists,
    then any other saved rows.
    """
    spend: dict[tuple[str, str], dict[str, Any]] = {}
    for sr in spend_rows:
        spend[_spend_key(sr.agent_kind, sr.agent_ref)] = {
            "total_tokens": sr.total_tokens,
            "cost_cents": sr.cost_cents,
            "calls": sr.calls,
        }

    by_key: dict[tuple[str, str], Any] = {
        _spend_key(r.agent_kind, r.agent_ref): r for r in rows
    }

    out: list[AgentGovernanceOut] = []
    seen: set[tuple[str, str]] = set()

    # Well-known agents first, in a stable order, saved-or-default.
    for agent_kind, agent_ref in _well_known_agents(brand_slugs):
        key = _spend_key(agent_kind, agent_ref)
        seen.add(key)
        out.append(
            _governance_out(
                agent_kind=agent_kind,
                agent_ref=agent_ref,
                row=by_key.get(key),
                spend=spend,
            )
        )

    # Any saved rows that are not in the well-known set (e.g. a brand-owned
    # identity agent, or a steward for a brand no longer in the workspace).
    for key, row in by_key.items():
        if key in seen:
            continue
        seen.add(key)
        out.append(
            _governance_out(
                agent_kind=row.agent_kind,
                agent_ref=row.agent_ref,
                row=row,
                spend=spend,
            )
        )
    return out


@router.get("/governance/agents", response_model=AgentGovernanceList)
async def list_governance(principal: _Auth) -> AgentGovernanceList:
    """Every governed agent in the workspace, with its real 7-day spend.

    Returns the Director, each brand's Steward, and every specialist, each as
    either its saved governance contract or its honest defaults (is_default),
    with the last 7 days of metered spend joined in. An unscoped / anonymous
    caller (local dev, the CLI, no membership) has no workspace, so this returns
    an empty list. Fails safe: any database problem yields an empty list rather
    than a 500, the same posture as the rest of the workspace-scoped surface.
    """
    if not principal.workspace_id:
        return AgentGovernanceList()
    since = datetime.now(UTC) - timedelta(days=_SPEND_WINDOW_DAYS)
    try:
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            async with workspace_session(session, principal.workspace_id) as scoped:
                rows = list(
                    await repo.list_agent_governance(scoped, principal.workspace_id)
                )
                spend_rows = list(
                    await repo.agent_spend_since(
                        scoped, principal.workspace_id, since
                    )
                )
                brand_slugs = sorted(
                    await repo.list_brand_slugs(scoped, principal.workspace_id)
                )
    except SQLAlchemyError:
        _log.warning("governance read failed; returning empty governance list")
        return AgentGovernanceList()
    return AgentGovernanceList(
        governance=_merge_governance_with_spend(
            rows=rows, spend_rows=spend_rows, brand_slugs=brand_slugs
        )
    )


@router.put(
    "/governance/agents/{agent_kind}/{agent_ref:path}",
    response_model=AgentGovernanceOut,
)
async def put_governance(
    agent_kind: str,
    agent_ref: str,
    body: AgentGovernanceUpdate,
    principal: _Owner,
) -> AgentGovernanceOut:
    """Set one agent's governance contract, returning it with its 7-day spend.

    Upserts the (workspace, agent_kind, agent_ref) row from the supplied fields
    (only the fields present in the body are changed; a null weekly_budget_cents
    or model clears that setting, an omitted field leaves it untouched). 403 when
    the caller has no workspace; 422 on an unknown agent_kind, an autonomy outside
    the known levels, or a negative budget. ``updated_by`` is stamped with the
    caller's uc_uid and a best-effort activity event is appended. The
    ``{agent_ref:path}`` converter lets an identity ref carry a slash.
    """
    if not principal.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="a workspace is required to set agent governance",
        )

    error = governance.validate_governance_update(
        agent_kind=agent_kind,
        autonomy=body.autonomy,
        weekly_budget_cents=body.weekly_budget_cents,
    )
    if error is not None:
        raise HTTPException(status_code=_HTTP_422, detail=error)

    # Distinguish "field omitted from the body" (leave unchanged) from "field sent
    # as null" (clear it) for the two nullable columns, using the sentinel the
    # repository understands. Pydantic's model_fields_set records which keys the
    # client actually sent.
    sent = body.model_fields_set
    weekly_budget = (
        body.weekly_budget_cents if "weekly_budget_cents" in sent else repo._UNSET
    )
    model = body.model if "model" in sent else repo._UNSET
    standing_orders = (
        body.standing_orders if "standing_orders" in sent else repo._UNSET
    )

    since = datetime.now(UTC) - timedelta(days=_SPEND_WINDOW_DAYS)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        async with workspace_session(session, principal.workspace_id) as scoped:
            await repo.upsert_agent_governance(
                scoped,
                principal.workspace_id,
                agent_kind=agent_kind,
                agent_ref=agent_ref,
                autonomy=body.autonomy,
                standing_orders=standing_orders,
                weekly_budget_cents=weekly_budget,
                model=model,
                updated_by=principal.uc_uid,
            )
            saved = await repo.get_agent_governance(
                scoped,
                principal.workspace_id,
                agent_kind=agent_kind,
                agent_ref=agent_ref,
            )
            spend_rows = list(
                await repo.agent_spend_since(scoped, principal.workspace_id, since)
            )

    _emit_governance_event(agent_kind, agent_ref)

    spend: dict[tuple[str, str], dict[str, Any]] = {}
    for sr in spend_rows:
        spend[_spend_key(sr.agent_kind, sr.agent_ref)] = {
            "total_tokens": sr.total_tokens,
            "cost_cents": sr.cost_cents,
            "calls": sr.calls,
        }
    return _governance_out(
        agent_kind=agent_kind, agent_ref=agent_ref, row=saved, spend=spend
    )


def _emit_governance_event(agent_kind: str, agent_ref: str) -> None:
    """Append one activity event for a governance change. Best-effort, never raises."""
    try:
        append_event(
            "you",
            "human",
            "set agent governance",
            brand=agent_ref if agent_kind == "steward" else None,
            target=f"{agent_kind}/{agent_ref}",
            note=None,
            ts=datetime.now(UTC).isoformat(),
        )
    except Exception:  # noqa: BLE001 - the feed is advisory; never fail the write
        pass
