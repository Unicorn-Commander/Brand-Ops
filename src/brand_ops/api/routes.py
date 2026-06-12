"""REST routes over the generation engine.

Every route is a thin adapter: it validates input, calls ``engine.generate_asset``
or a loader, and maps domain results and errors onto HTTP. The engine is the one
shared entry point the CLI, the MCP server, and the autonomy agents also use, so
the human path and the agent path can never drift. This module calls no provider
SDK directly and reads no secret; backends self-report health and a missing key
becomes a clean 422, never a 500.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response

from .. import agent_identity, engine
from ..activity import append_event, read_events
from ..agents.brand_intake import BrandIntakeAgent
from ..agents.brand_research import _slugify
from ..agents.director import DirectorAgent
from ..agents.registry import AGENT_REGISTRY
from ..asset_types import ASSET_TYPES, get_asset_spec
from ..backends.base import BackendError, BackendNotConfigured
from ..brand_clone import clone_brand, create_brand_from_draft
from ..brand_loader import DEFAULT_KITS_DIR, BrandNotFound, list_brands, load_brand
from ..config import get_settings
from ..compliance import (
    build_rules,
    build_tokens,
    check_asset,
    check_text_target,
    transition,
)
from ..models import Brand
from ..templates import list_templates
from .agents import UnknownAgent, run_agent
from .auth import Principal, require_principal
from .catalog import _coerce_record, read_assets
from .guidelines import build_guidelines
from .schemas import (
    MeOut,
    ActivityEvent,
    ActivityList,
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
    DirectorChatRequest,
    DirectorReply,
    GeneratedFile,
    GenerateRequest,
    GenerateResult,
    IntakeDraft,
    IntakeRequest,
    IntakeResult,
    PromptPreview,
    TemplateList,
    TransitionRequest,
    TransitionResult,
    UploadResult,
)
from .upload import MAX_UPLOAD_BYTES, UploadError, save_upload

router = APIRouter(prefix="/api", tags=["brand-ops"])

# 422 as a literal. The named constant was renamed across Starlette versions
# (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT); the integer is stable and
# version-proof across the pinned fastapi range. Used for "valid request,
# unprocessable input" (unknown asset/brand-shape, backend needs a key).
_HTTP_422 = 422

# Auth is applied to every route as a dependency. It resolves to the anonymous
# principal when AUTH_REQUIRED is false, so the surface stays uniform whether or
# not auth is on.
_Auth = Annotated[Principal, Depends(require_principal)]

# Extensions a browser safely renders as a picture in an <img> tag: these stay
# inline so the GUI keeps working. Anything else (an SVG above all, which can
# carry <script>) is served as a forced download, so a stored-XSS payload in an
# uploaded asset cannot execute on a shared origin once auth is on.
_INLINE_RASTER_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _safe_file_response(path: Path) -> FileResponse:
    """Serve a file from disk with sniff-proof, XSS-safe headers.

    ``X-Content-Type-Options: nosniff`` is always set so a browser honors the
    declared Content-Type rather than sniffing the bytes into HTML. A raster
    image (png/jpeg/webp/gif) is served inline so ``<img>`` tags render it; any
    other type — notably ``.svg``, which can embed script — gets
    ``Content-Disposition: attachment`` so the browser downloads it instead of
    rendering (and executing) it inline. The decision is on the stored
    extension, which the upload path derives from the validated content type,
    not from the untrusted uploaded name.
    """
    headers = {"X-Content-Type-Options": "nosniff"}
    if path.suffix.lower() not in _INLINE_RASTER_EXTS:
        headers["Content-Disposition"] = "attachment"
    return FileResponse(path, headers=headers)


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
    )


def _brand_summary(brand: Brand) -> BrandSummary:
    meta = brand.brand
    return BrandSummary(
        slug=meta.slug,
        display_name=meta.display_name,
        tagline=meta.tagline,
        parent=meta.parent,
        mark_url=_mark_url(brand),
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
        org=principal.org,
        issuer=principal.issuer,
        anonymous=principal.anonymous,
        scopes=list(principal.scopes),
        logout_url=logout,
    )


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
def clone_brand_route(slug: str, body: CloneBrandRequest, _: _Auth) -> BrandDetail:
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
async def intake_brand_route(body: IntakeRequest, _: _Auth) -> IntakeResult:
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
    if (DEFAULT_KITS_DIR / slug).exists():
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
        draft, notes = await BrandIntakeAgent().draft_identity(
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


@router.get("/brands/{slug}/compliance", response_model=BrandComplianceRollup)
def get_brand_compliance(slug: str, _: _Auth) -> BrandComplianceRollup:
    """Run the compliance check over every catalog asset of the brand.

    Iterates the brand's catalog, scores each asset deterministically, and rolls
    the verdicts into a summary (pass/warn/fail/skipped counts and an average
    score) plus a per-asset row list. The same engine POST /compliance/check
    uses, applied brand-wide.
    """
    brand = _load_brand_or_404(slug)
    rules = build_rules(brand)

    counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    rows: list[ComplianceAssetRow] = []
    scores: list[int] = []

    for record in read_assets(brand=slug):
        result = check_asset(
            slug, record.file, record.asset_type, record.kind, rules
        )
        result_status = result["status"]
        counts[result_status] = counts.get(result_status, 0) + 1
        # A skipped asset (e.g. an SVG) carries no meaningful score; leave it out
        # of the average so vectors don't drag the brand's number down.
        if result_status != "skipped":
            scores.append(int(result["score"]))
        rows.append(
            ComplianceAssetRow(
                file=record.file,
                asset_type=record.asset_type,
                kind=record.kind,
                status=result_status,
                score=int(result["score"]),
                violations=list(result["violations"]),
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
def generate(body: GenerateRequest, _: _Auth) -> GenerateResult:
    """Generate the asset live, persisting the files and the catalog sidecars.

    When the chosen backend has no credentials, the engine raises
    ``BackendNotConfigured``; that becomes an HTTP 422 with a clear needs-key
    body rather than a 500, so a caller knows exactly which key to supply.
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
            count=body.count,
            dry_run=False,
        )
    except BrandNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=_HTTP_422, detail=str(exc)
        ) from None
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
    except BackendError as exc:
        # A real provider failure (network, quota, bad response). Surface it as a
        # 502: the request was valid, the upstream backend could not fulfill it.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"backend error: {exc}"
        ) from None

    result = outcome.result
    files = [
        GeneratedFile(path=str(p), catalog_path=str(p) + ".json") for p in outcome.paths
    ]
    return GenerateResult(
        brand=body.brand,
        asset=body.asset,
        variant=outcome.built.variant,
        backend=result.backend if result else outcome.built.backend,
        model=result.model if result else None,
        mime=result.mime if result else None,
        cost_cents=result.cost_cents if result else None,
        files=files,
        missing_refs=outcome.missing_refs,
        warnings=outcome.built.warnings,
    )


# ---------------------------------------------------------------------------
# Asset catalog
# ---------------------------------------------------------------------------


@router.get("/assets", response_model=AssetList)
def get_assets(
    _: _Auth,
    brand: Annotated[str | None, Query()] = None,
    asset: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
) -> AssetList:
    """List generated assets from the on-disk catalog sidecars.

    Optional ``brand`` and ``asset`` filters narrow the scan. The optional
    ``status`` filter ("approved" or "needs_review") returns only assets whose
    lifecycle status matches; a never-stamped asset (null status) is excluded by
    any status filter. The records mirror the sidecar fields the output manager
    writes, so this is the same data WS1 will later serve from Postgres.
    """
    if status is not None and status not in ("approved", "needs_review"):
        raise HTTPException(
            status_code=_HTTP_422,
            detail="status must be approved or needs_review",
        )
    return AssetList(assets=read_assets(brand=brand, asset=asset, status=status))


@router.get("/assets/file")
def get_asset_file(
    _: _Auth,
    brand: Annotated[str, Query()],
    asset: Annotated[str, Query()],
    file: Annotated[str, Query()],
    kind: Annotated[str, Query()] = "generated",
) -> FileResponse:
    """Serve one asset's bytes, confined to the brand-kits tree.

    Resolves ``brand-kits/<brand>/<kind>/<asset>/<file>`` and refuses anything
    that escapes the kits root, so a crafted path cannot read outside it. Auth is
    the same anonymous-or-required dependency as the rest of the API; serving
    tenant-scoped assets behind real auth will want signed URLs at deploy.
    """
    if kind not in ("generated", "imported"):
        raise HTTPException(status_code=_HTTP_422, detail="kind must be generated or imported")
    root = DEFAULT_KITS_DIR.resolve()
    target = (root / brand / kind / asset / file).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="asset not found"
        )
    return _safe_file_response(target)


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
    # image path the catalog and approve flow resolve.
    root = DEFAULT_KITS_DIR.resolve()
    image_path = (root / brand / kind / asset / file).resolve()
    sidecar = image_path.with_suffix(image_path.suffix + ".json")
    record = _coerce_record(data, sidecar, image_path, kind=kind)
    if record is None:  # pragma: no cover - transition just wrote a valid record
        raise HTTPException(
            status_code=_HTTP_422, detail="sidecar is not an asset record"
        )
    return record


@router.post("/assets/transition", response_model=TransitionResult)
def transition_asset(body: TransitionRequest, _: _Auth) -> TransitionResult:
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
def approve_asset(body: ApproveRequest, _: _Auth) -> ApproveResult:
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
    and a missing log is simply an empty feed.
    """
    events = [ActivityEvent.model_validate(e) for e in read_events(brand=brand, limit=limit)]
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
    _: _Auth,
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
    try:
        result = await run_agent(name, payload)
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


@router.post("/director/chat", response_model=DirectorReply)
async def director_chat(body: DirectorChatRequest, _: _Auth) -> DirectorReply:
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
    payload = {
        "message": body.message,
        "brand": body.brand,
        "history": history,
        "brands_context": _brands_context(),
    }
    try:
        result = await DirectorAgent().run(payload)
    except Exception:  # noqa: BLE001 - an LLM/agent problem degrades to a 200 fallback, never a 500
        result = {
            "reply": (
                "I hit a snag reaching my planner. Tell me the brand and what "
                "you need and I will line it up."
            ),
            "intent": "chat",
            "brand": body.brand,
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
        }

    # When Iris classified the turn as creating an agent, act on it here: stand
    # up (or surface) the on-brand identity through agent_identity and rewrite
    # the reply in her voice. This is the agentic half of agent identity on the
    # Director surface, co-equal with the GUI's create form. Any failure degrades
    # to a normal chat reply inside the helper, so the route still answers 200.
    if result.get("intent") == "agent":
        result = _fulfill_agent_intent(result)

    # Record what Iris did on the provenance feed: the intent she routed the turn
    # to. Emitted here at the route (the director's flow) rather than inside the
    # agent's run, so the agent's unit tests stay side-effect free. Best-effort:
    # a feed failure never changes the reply the Studio receives.
    _emit_director_event(result)

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


# The human-readable verb the activity feed shows for each director intent. Iris
# either routed the turn to work (single/pack/research) or simply talked.
_DIRECTOR_ACTION_FOR_INTENT = {
    "chat": "chatted",
    "single": "routed a single asset",
    "pack": "routed a pack",
    "research": "routed research",
    "agent": "created agent",
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
def create_brand_agent(slug: str, body: CreateAgentIdentityRequest, _: _Auth) -> AgentIdentityOut:
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
def generate_brand_agent_avatar(slug: str, agent: str, _: _Auth) -> AgentIdentityOut:
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
    root = DEFAULT_KITS_DIR.resolve()
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
    try:
        path = agent_identity.avatar_path(slug, agent)
    except agent_identity.AgentIdentityError as exc:
        raise HTTPException(status_code=_HTTP_422, detail=str(exc)) from None
    return _serve_avatar(path, size)


@router.post("/brands/{slug}/agents/{agent}/variants", response_model=AgentIdentityOut)
def generate_brand_agent_variants(slug: str, agent: str, _: _Auth) -> AgentIdentityOut:
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
