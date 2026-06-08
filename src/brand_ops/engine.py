"""The generation engine. One entry point that every interface calls.

The CLI, the MCP server, the REST API, and the autonomy agents all call
generate_asset, so the agent path and the human path can never drift. The
engine loads the brand, composes the prompt, resolves references, picks the
backend, and (unless dry_run) generates and persists the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .backends.base import BackendNotConfigured, GenRequest, GenResult, load_backend
from .brand_loader import load_brand, resolve_reference_images
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


def _wants_vector(backend: str, asset_type: str) -> bool:
    # Recraft can emit true SVG; use it for the logo lane where vector is ideal.
    return backend == "recraft" and asset_type == "logo"


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

    backend_impl = load_backend(built.backend, settings)
    if not backend_impl.health():
        raise BackendNotConfigured(
            f"backend {built.backend!r} is not configured. "
            f"Set its API key or endpoint, then try again."
        )

    req = GenRequest(
        prompt=built.text,
        width=built.size[0],
        height=built.size[1],
        transparent=built.transparent,
        reference_images=built.reference_images,
        count=max(1, count),
        vector=vector,
        extra={"colors": [brand.visual.colors.primary, brand.visual.colors.accent]},
    )
    result = backend_impl.generate(req)
    outcome.result = result
    outcome.paths = save_result(brand_slug, asset_type, variant, built, result, kits_dir)
    return outcome
