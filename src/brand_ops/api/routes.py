"""REST routes over the generation engine.

Every route is a thin adapter: it validates input, calls ``engine.generate_asset``
or a loader, and maps domain results and errors onto HTTP. The engine is the one
shared entry point the CLI, the MCP server, and the autonomy agents also use, so
the human path and the agent path can never drift. This module calls no provider
SDK directly and reads no secret; backends self-report health and a missing key
becomes a clean 422, never a 500.
"""
from __future__ import annotations

from typing import Annotated, Any

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
from fastapi.responses import FileResponse

from .. import engine
from ..agents.registry import AGENT_REGISTRY
from ..asset_types import ASSET_TYPES, get_asset_spec
from ..backends.base import BackendError, BackendNotConfigured
from ..brand_loader import DEFAULT_KITS_DIR, BrandNotFound, list_brands, load_brand
from ..models import Brand
from .agents import UnknownAgent, run_agent
from .auth import Principal, require_principal
from .catalog import read_assets
from .schemas import (
    AgentInfo,
    AgentList,
    AgentRunResult,
    AssetList,
    AssetTypeList,
    AssetTypeOut,
    BrandColorsOut,
    BrandDetail,
    BrandList,
    BrandSummary,
    BrandTypographyOut,
    BrandVisualOut,
    BrandVoiceOut,
    GeneratedFile,
    GenerateRequest,
    GenerateResult,
    PromptPreview,
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


def _brand_summary(brand: Brand) -> BrandSummary:
    meta = brand.brand
    return BrandSummary(
        slug=meta.slug,
        display_name=meta.display_name,
        tagline=meta.tagline,
        parent=meta.parent,
    )


def _brand_detail(brand: Brand) -> BrandDetail:
    meta = brand.brand
    visual = brand.visual
    return BrandDetail(
        slug=meta.slug,
        display_name=meta.display_name,
        tagline=meta.tagline,
        parent=meta.parent,
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
) -> AssetList:
    """List generated assets from the on-disk catalog sidecars.

    Optional ``brand`` and ``asset`` filters narrow the scan. The records mirror
    the sidecar fields the output manager writes, so this is the same data WS1
    will later serve from Postgres.
    """
    return AssetList(assets=read_assets(brand=brand, asset=asset))


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
    return FileResponse(target)


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
