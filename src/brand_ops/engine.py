"""The generation engine. One entry point that every interface calls.

The CLI, the MCP server, the REST API, and the autonomy agents all call
generate_asset, so the agent path and the human path can never drift. The
engine loads the brand, composes the prompt, resolves references, picks the
backend, and (unless dry_run) generates and persists the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import cards, cost
from .asset_types import get_asset_spec
from .backends.base import BackendNotConfigured, GenRequest, GenResult, load_backend
from .brand_loader import (
    current_kits_dir,
    get_current_workspace_id,
    load_brand,
    resolve_reference_images,
    safe_subpath,
)
from .config import Settings, get_settings
from .output import generated_dir, output_name, save_result
from .prompt_builder import BuiltPrompt, build_prompt


@dataclass
class AssetOutcome:
    built: BuiltPrompt
    planned_path: Path
    missing_refs: list[str]
    result: GenResult | None = None
    paths: list[Path] = field(default_factory=list)
    # Human-readable notes about the run, e.g. a backend fallback.
    notes: list[str] = field(default_factory=list)


def _wants_vector(backend: str, asset_type: str) -> bool:
    # Recraft can emit true SVG; use it for the logo lane where vector is ideal.
    return backend == "recraft" and asset_type == "logo"


def resolve_image_backend(default_backend, requested, settings):
    """Resolve the image backend, applying the fallback chain.

    Returns (active_name, backend_impl, note). When the default backend has no
    key and the caller did not request one explicitly, fall through
    settings.fallback_image_backends (the LiteLLM gateway first when configured,
    then OpenRouter) and use the first healthy one. An explicit request is never
    swapped. Shared by generate_asset and the agent-identity avatar path.
    """
    active = default_backend
    impl = load_backend(active, settings)
    note = None
    if not impl.health() and requested is None:
        for fallback in settings.fallback_image_backends:
            if fallback == active:
                continue
            fallback_impl = load_backend(fallback, settings)
            if fallback_impl.health():
                note = f"{active} backend is not configured; generated via {fallback} instead"
                active, impl = fallback, fallback_impl
                break
    if not impl.health():
        raise BackendNotConfigured(
            f"backend {active!r} is not configured. "
            f"Set its API key or endpoint, then try again."
        )
    return active, impl, note


def generate_asset(
    brand_slug: str,
    asset_type: str,
    *,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
    count: int = 1,
    dry_run: bool = False,
    settings: Settings | None = None,
    kits_dir: Path | None = None,
) -> AssetOutcome:
    settings = settings or get_settings()
    brand = load_brand(brand_slug, kits_dir)
    refs, missing = resolve_reference_images(brand_slug, brand, kits_dir)
    built = build_prompt(
        brand,
        asset_type,
        variant=variant,
        topic=topic,
        platform=platform,
        reference_images=list(refs.values()),
        backend=backend,
    )

    vector = _wants_vector(built.backend, asset_type)
    planned_mime = "image/svg+xml" if vector else "image/png"
    planned_path = generated_dir(brand_slug, asset_type, kits_dir) / output_name(
        asset_type, variant, planned_mime, "<timestamp>"
    )
    outcome = AssetOutcome(built=built, planned_path=planned_path, missing_refs=missing)
    if dry_run:
        return outcome

    # Resolve the backend (with the fallback chain). An explicit backend= is
    # respected and never swapped.
    active_backend, backend_impl, note = resolve_image_backend(built.backend, backend, settings)
    if note:
        outcome.notes.append(note)

    # Per-tenant cost governance around the (paid) backend call. The cap read
    # (before) and the meter write (after) run on ONE event loop opened by
    # ``image_governance`` for this generation, so asyncpg reuses its loop-bound
    # pool across the two DB touches instead of rebuilding it for each. Ordering
    # is unchanged: ``enforce`` denies an over-budget run before the backend, and
    # ``meter`` records the spend after the bytes are saved. Default-disabled
    # (cap 0, null budgets) and a no-op when unscoped (CLI / stdio MCP / tests),
    # where no loop or DB is touched at all.
    workspace_id = get_current_workspace_id()
    with cost.image_governance(workspace_id) as governor:
        # BEFORE the backend: deny when this workspace's image spend over the
        # rolling window already meets its cap or the attributed agent's weekly
        # budget. Raises QuotaExceeded (a BackendError) on a breach, which the
        # existing REST/MCP handlers already map to a clean 4xx.
        governor.enforce()

        # Recompute vector against the backend that will actually run (only
        # recraft emits SVG; a fallback off recraft produces raster).
        vector = _wants_vector(active_backend, asset_type)
        req = GenRequest(
            prompt=built.text,
            width=built.size[0],
            height=built.size[1],
            transparent=built.transparent,
            reference_images=built.reference_images,
            count=max(1, count),
            vector=vector,
            extra={
                "colors": [brand.visual.colors.primary, brand.visual.colors.accent],
                # Attribute the gateway spend to this workspace (the litellm
                # backend forwards it as the OpenAI ``user`` field). Empty when
                # unscoped, in which case the backend simply omits it. Never a
                # secret: the workspace id is an opaque uuid.
                "user": workspace_id or "",
            },
        )
        result = backend_impl.generate(req)
        # Transparent asset types (logo, app_icon, ...) must end up with a real
        # alpha channel even when the backend can only return an opaque image on a
        # flat field. The gateway's gpt-image-1 path cannot honor a
        # transparent-background request, so cut a uniform background out here. It
        # is a no-op when the image is already transparent or the background is
        # not a single uniform color.
        if built.transparent and result.images:
            from .imaging import knockout_background

            result.images = [knockout_background(img) for img in result.images]
        outcome.result = result
        outcome.paths = save_result(
            brand_slug, asset_type, variant, built, result, kits_dir
        )

        # AFTER the bytes are written: meter the successful generation per
        # workspace, mirroring the text path's chat metering. One
        # agent_spend_event (kind=image) carrying the estimated cost +
        # model/backend, so the next request's cap check, the Crew rollup, and
        # Lago reconciliation all see this charge. Best-effort: a metering failure
        # never undoes a generation that already succeeded. No-op when unscoped.
        governor.meter(
            brand_slug=brand_slug,
            backend=result.backend,
            model=result.model,
            cost_cents=result.cost_cents,
            count=len(result.images) or max(1, count),
        )
    return outcome


def _resolve_mark(brand_slug, brand, mark, kits_dir):
    """Find the brand mark to composite: an explicit one, the brand's declared
    mark, else the most recent generated (then imported) logo. None if there is
    no logo yet (the card still renders, just without a mark)."""
    base = (kits_dir or current_kits_dir()) / brand_slug
    if mark:
        # ``mark`` is caller-supplied. Resolve it ONLY as a filename under this
        # brand's own logo dirs, confined to the kits tree: an absolute path or a
        # traversing name is rejected, so a card can never composite another
        # tenant's (or an arbitrary host) file. (Was: Path(mark).is_file() honored
        # any absolute path, and base/sub/mark allowed ".." to escape.)
        for sub in ("generated", "imported"):
            cand = safe_subpath(base, sub, "logo", mark)
            if cand is not None and cand.is_file():
                return cand
    declared = getattr(brand.brand, "mark", None)
    if declared and getattr(declared, "file", None):
        kind = declared.kind or "imported"
        atype = declared.asset_type or "logo"
        cand = safe_subpath(base, kind, atype, declared.file)
        if cand is not None and cand.is_file():
            return cand
    for sub in ("generated/logo", "imported/logo"):
        directory = base / sub
        if directory.is_dir():
            pngs = sorted(directory.glob("*.png"))
            if pngs:
                return pngs[-1]
    return None


def render_card(
    brand_slug: str,
    asset_type: str,
    *,
    message: str | None = None,
    mark: str | None = None,
    kits_dir: Path | None = None,
) -> tuple[bytes, Path | None]:
    """Compose an og_image / social_card deterministically; return (PNG bytes, mark path).

    The pure render with no catalog write: the brand's real fonts and exact colors
    are drawn over a brand background with the mark composited in, so the copy is
    always correct and on brand. Use this to produce a build-time file at an
    arbitrary path (e.g. the web app's default link-preview image at
    ``web/public/og.png``). :func:`generate_card` wraps this to also save the
    result into the brand's managed catalog.
    """
    brand = load_brand(brand_slug, kits_dir)
    if not cards.supported(asset_type):
        raise ValueError(
            f"{asset_type!r} has no card layout; composable: og_image, social_card"
        )
    mark_path = _resolve_mark(brand_slug, brand, mark, kits_dir)
    png = cards.compose_card(brand, asset_type, message=message, mark_path=mark_path)
    return png, mark_path


def generate_card(
    brand_slug: str,
    asset_type: str,
    *,
    message: str | None = None,
    mark: str | None = None,
    variant: str | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
    kits_dir: Path | None = None,
) -> AssetOutcome:
    """Compose a text-bearing card (og_image / social_card) deterministically.

    Unlike :func:`generate_asset`, the text is rendered with the brand's real
    fonts and exact colors over a brand background with the mark composited in,
    so the copy is always correct and on brand (no model text rendering). Saves
    into the same catalog so it shows in the GUI and gets a compliance stamp.
    """
    settings = settings or get_settings()
    brand = load_brand(brand_slug, kits_dir)
    if not cards.supported(asset_type):
        raise ValueError(
            f"{asset_type!r} has no card layout; composable: og_image, social_card"
        )

    spec = get_asset_spec(asset_type)
    text = message or brand.brand.tagline or ""
    built = BuiltPrompt(
        text=text,
        asset_type=asset_type,
        variant=variant,
        backend="compose",
        size=spec.size,
        transparent=False,
        reference_images=[],
        warnings=[],
    )
    planned_path = generated_dir(brand_slug, asset_type, kits_dir) / output_name(
        asset_type, variant, "image/png", "<timestamp>"
    )
    outcome = AssetOutcome(built=built, planned_path=planned_path, missing_refs=[])
    if dry_run:
        return outcome

    png, mark_path = render_card(
        brand_slug, asset_type, message=message, mark=mark, kits_dir=kits_dir
    )
    result = GenResult(
        images=[png],
        mime="image/png",
        backend="compose",
        model="brand-ops-compose",
        meta={
            "composited": True,
            "mark": (str(mark_path) if mark_path else None),
            "size": list(spec.size),
        },
    )
    outcome.result = result
    if mark_path:
        outcome.notes.append(f"composited with mark {Path(mark_path).name}")
    outcome.paths = save_result(brand_slug, asset_type, variant, built, result, kits_dir)
    return outcome
