"""Request and response shapes for the Brand-Ops REST API.

These are the wire contract. They mirror the engine's domain objects (Brand,
AssetSpec, BuiltPrompt, the catalog sidecar) without leaking filesystem Paths or
internal types, so a UI or a federated caller gets a stable JSON surface that
does not move when the engine's internals do.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Brands
# ---------------------------------------------------------------------------


class BrandColorsOut(BaseModel):
    primary: str
    accent: str
    background: str | None = None
    foreground: str | None = None
    muted: str | None = None


class BrandTypographyOut(BaseModel):
    primary_font: str
    display_font: str
    mono_font: str


class BrandVisualOut(BaseModel):
    colors: BrandColorsOut
    typography: BrandTypographyOut
    style_keywords: list[str] = Field(default_factory=list)


class BrandVoiceOut(BaseModel):
    tone: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)


class BrandSummary(BaseModel):
    """The compact form returned by the list endpoint."""

    slug: str
    display_name: str
    tagline: str | None = None
    parent: str | None = None


class BrandDetail(BrandSummary):
    """The full brand, returned by the by-slug endpoint."""

    description: str | None = None
    visual: BrandVisualOut
    voice: BrandVoiceOut
    prompt_assets: list[str] = Field(default_factory=list)
    reference_roles: list[str] = Field(default_factory=list)


class BrandList(BaseModel):
    brands: list[BrandSummary]


# ---------------------------------------------------------------------------
# Asset types
# ---------------------------------------------------------------------------


class AssetTypeOut(BaseModel):
    key: str
    label: str
    width: int
    height: int
    default_backend: str
    transparent: bool
    notes: str = ""


class AssetTypeList(BaseModel):
    asset_types: list[AssetTypeOut]


# ---------------------------------------------------------------------------
# Prompt preview + generate
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Body shared by prompt-preview (dry run) and generate (live)."""

    brand: str
    asset: str
    variant: str | None = None
    topic: str | None = None
    platform: str | None = None
    backend: str | None = None
    count: int = Field(default=1, ge=1, le=8)


class PromptPreview(BaseModel):
    """The dry-run result: the composed prompt and the plan, no spend."""

    brand: str
    asset: str
    variant: str | None = None
    backend: str
    width: int
    height: int
    transparent: bool
    prompt: str
    reference_images: list[str] = Field(default_factory=list)
    planned_path: str
    missing_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GeneratedFile(BaseModel):
    path: str
    catalog_path: str | None = None


class GenerateResult(BaseModel):
    """The live result: what was produced and how."""

    brand: str
    asset: str
    variant: str | None = None
    backend: str
    model: str | None = None
    mime: str | None = None
    cost_cents: float | None = None
    files: list[GeneratedFile] = Field(default_factory=list)
    missing_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Asset catalog (sidecar reads)
# ---------------------------------------------------------------------------


class AssetRecord(BaseModel):
    """One catalog entry, read from a generated sidecar JSON file."""

    brand: str
    asset_type: str
    variant: str | None = None
    backend: str | None = None
    model: str | None = None
    mime: str | None = None
    size: list[int] = Field(default_factory=list)
    transparent: bool | None = None
    cost_cents: float | None = None
    created_at: str | None = None
    file: str
    kind: str = "generated"
    url: str | None = None
    prompt: str | None = None
    reference_images: list[str] = Field(default_factory=list)


class AssetList(BaseModel):
    assets: list[AssetRecord]


# ---------------------------------------------------------------------------
# Asset upload (drag-and-drop into a brand kit)
# ---------------------------------------------------------------------------


class UploadResult(BaseModel):
    """The response from POST /api/assets/upload.

    Carries the new catalog entry under ``asset`` in the same ``AssetRecord``
    shape (and with the same ``/api/assets/file`` URL) that GET /api/assets
    serves, so the Library can drop it straight into its list without a reload.
    """

    asset: AssetRecord


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class AgentInfo(BaseModel):
    """One agent as the GUI sees it, projected from the Brigade registry.

    Mirrors the registry's ``AgentSpec`` minus the runtime class: the stable
    name, a human description, the JSON-schema input contract, the auth scopes
    a caller must hold, and the minimum suite tier. This is what GET
    /api/agents lists so the Studio can build a form per agent and gate it.
    """

    name: str
    description: str
    input_schema: dict = Field(default_factory=dict)
    scopes_required: list[str] = Field(default_factory=list)
    tier_required: str


class AgentList(BaseModel):
    agents: list[AgentInfo]


class AgentRunResult(BaseModel):
    """The result of POST /api/agents/{name}/run.

    ``result`` is the agent's own return dict, passed through verbatim so the
    GUI sees exactly what the agent produced (its keys vary by agent). ``status``
    is lifted out of that dict for convenience: agents stamp a ``status`` such
    as ``ok``, ``skipped``, ``research_only``, ``unparsed``, or ``error``, and
    a ``skipped`` from a missing LLM gateway is a normal 200, not a failure.
    """

    agent: str
    status: str
    result: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Errors + health
# ---------------------------------------------------------------------------


class HealthOut(BaseModel):
    status: str = "ok"
    service: str = "brand-ops"
    version: str
    auth_required: bool


class NeedsKeyError(BaseModel):
    """Returned (HTTP 422) when a backend has no credentials configured."""

    error: str = "backend_not_configured"
    backend: str
    detail: str
