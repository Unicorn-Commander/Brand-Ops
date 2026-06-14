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
    # The brand's canonical mark as a ready-to-render URL into /api/assets/file,
    # or null when the brand declares no mark. The GUI prefers this over guessing
    # a logo from the (cross-mis-tagged) catalog.
    mark_url: str | None = None


class BrandDetail(BrandSummary):
    """The full brand, returned by the by-slug endpoint."""

    description: str | None = None
    visual: BrandVisualOut
    voice: BrandVoiceOut
    prompt_assets: list[str] = Field(default_factory=list)
    reference_roles: list[str] = Field(default_factory=list)


class BrandList(BaseModel):
    brands: list[BrandSummary]


class CloneBrandRequest(BaseModel):
    """Body for POST /api/brands/{slug}/clone: stand up a new brand from {slug}.

    ``new_slug`` is the new brand's identifier (lowercase letters, digits, and
    hyphens; an invalid value is a 422) and ``display_name`` its human name.
    ``as_child`` (default True) writes a minimal child that inherits {slug}'s
    visual and voice through the loader merge; False copies them in as a distinct
    brand with no parent. The source mark is never copied — the new brand starts
    with no logo, so its ``mark_url`` is null until one is imported.
    """

    new_slug: str
    display_name: str
    as_child: bool = True


class IntakeRequest(BaseModel):
    """Body for POST /api/brands/intake: draft an identity and create the brand.

    ``name`` is the new brand's display name (required); a slug is derived from
    it (lowercase letters, digits, hyphens) and a name that yields an invalid
    slug is a 422. ``description`` is whatever the operator can say about the
    brand, ``url`` a related URL or the live site. ``parent`` is an existing
    brand slug when this is a sub-brand: the Director is given the parent's
    palette and voice and drafts a consistent but distinct identity, and the new
    brand records the parent. ``mode`` is ``describe`` (draft from the name and
    description, the default) or ``onboard`` (draft from the url and name as
    hints, with a note that deep live-site capture needs Brigade).
    """

    name: str
    description: str | None = None
    url: str | None = None
    parent: str | None = None
    mode: str = "describe"


class IntakeDraft(BaseModel):
    """What the Director drafted, for the conversation to narrate.

    The visual and verbal identity the brand was created from: the five-role
    ``palette`` (hex), the three ``typography`` fonts, the ``voice_tone`` and
    ``style_keywords`` lists, a plain ``tagline``, and a 1 to 3 sentence
    ``rationale`` on the choices. Every palette value is a valid hex (the writer
    validates against the BrandColors rule), so the same draft that narrates the
    conversation is exactly what was written.
    """

    palette: BrandColorsOut
    typography: BrandTypographyOut
    voice_tone: list[str] = Field(default_factory=list)
    style_keywords: list[str] = Field(default_factory=list)
    tagline: str = ""
    rationale: str = ""


class IntakeResult(BaseModel):
    """The response from POST /api/brands/intake.

    ``brand`` is the newly created brand in the same full ``BrandDetail`` shape
    GET /api/brands/{slug} serves, so the Studio can drop it straight into its
    brand list. ``draft`` is what the Director drafted (for the chat to narrate),
    and ``notes`` are honest notes about how the identity was produced, for
    example that a live-site capture needs Brigade or that the identity is a
    neutral starting point to refine.
    """

    brand: BrandDetail
    draft: IntakeDraft
    notes: list[str] = Field(default_factory=list)


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
# Templates (data-driven, locked layouts the frontend renders from brand tokens)
# ---------------------------------------------------------------------------


class TemplateSlot(BaseModel):
    """One placeable element in a template (GET /api/templates).

    ``kind`` is ``text`` (an editable string) or ``mark`` (the brand logo image,
    locked). ``x``/``y`` anchor the slot and ``w`` is its max width, all 0..1
    fractions of the canvas so the layout scales. For a text slot,
    ``font``/``size``/``color``/``align`` are the locked typesetting and
    ``default``/``max_len`` seed and bound the editable copy; those are null for
    a mark slot. ``color`` is a brand color token name (e.g. ``text/foreground``)
    and ``font`` is a brand font role (``display`` or ``body``), never raw hex,
    so the render is on-brand by construction.
    """

    key: str
    kind: str
    label: str
    default: str | None = None
    max_len: int | None = None
    x: float
    y: float
    w: float
    align: str | None = None
    font: str | None = None
    size: float | None = None
    color: str | None = None


class TemplateBackground(BaseModel):
    """A template's background fill.

    ``kind`` is ``brand-gradient`` (the brand/primary -> brand/accent sweep, with
    ``color`` null) or ``solid`` (a flat fill whose ``color`` is a color token
    name like ``surface/background``).
    """

    kind: str
    color: str | None = None


class TemplateDef(BaseModel):
    """A locked, data-driven layout the frontend renders from brand tokens.

    ``asset_type`` maps a saved render onto a catalog asset type (e.g.
    ``og_image``, ``social_card``) so it flows through the same library and
    compliance gate as a generated asset. ``width``/``height`` are the pixel
    canvas size; ``slots`` are positioned by 0..1 fractions so the layout scales
    to any size.
    """

    id: str
    label: str
    asset_type: str
    width: int
    height: int
    background: TemplateBackground
    slots: list[TemplateSlot] = Field(default_factory=list)


class TemplateList(BaseModel):
    """The response from GET /api/templates: the locked template set."""

    templates: list[TemplateDef] = Field(default_factory=list)


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


class GenerateCardRequest(BaseModel):
    """Body for the deterministic, keyless card compositor (POST /generate-card).

    Composes ``og_image`` or ``social_card`` from the brand's real fonts, exact
    colors, and mark, with no provider key and no spend. ``message`` defaults to
    the brand tagline. ``mark`` overrides the composited mark; ``variant`` names
    the output variant.
    """

    brand: str
    asset: str
    message: str | None = None
    mark: str | None = None
    variant: str | None = None


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


class ComplianceSummary(BaseModel):
    """The small compliance summary stamped onto an asset's sidecar.

    A recomputed-at-stamp-time digest of the deterministic verdict: the overall
    ``status`` (pass/warn/fail/skipped), the 0..100 ``score``, and the
    human-readable ``violations`` lines. The full per-rule breakdown lives in the
    ComplianceResult that POST /compliance/check returns; this is the at-a-glance
    form the catalog carries.
    """

    status: str
    score: int = 0
    violations: list[str] = Field(default_factory=list)


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
    # Lifecycle gate (derived from the compliance loop). null only when the asset
    # has never been stamped; "approved" on pass/warn/skipped, "needs_review" on
    # a compliance fail. ``compliance`` is the summary recomputed at stamp time.
    lifecycle_status: str | None = None
    compliance: ComplianceSummary | None = None


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


class ApproveRequest(BaseModel):
    """Body for POST /api/assets/approve: identify one asset to approve.

    The operator override of a needs_review asset. ``brand``/``file``/``asset``
    (the asset type) locate the sidecar; ``kind`` selects the generated or
    imported tree (default ``generated``), matching the rest of the asset API.
    """

    brand: str
    file: str
    asset: str
    kind: str = "generated"


class ApproveResult(BaseModel):
    """The response from POST /api/assets/approve: the refreshed catalog entry."""

    asset: AssetRecord


class TransitionRequest(BaseModel):
    """Body for POST /api/assets/transition: move one asset to a lifecycle state.

    ``brand``/``file``/``asset`` (the asset type) locate the sidecar; ``kind``
    selects the generated or imported tree (default ``generated``). ``to`` is the
    target lifecycle state — one of needs_review, approved, changes_requested,
    archived — validated server-side (a bad state is a 422). ``note`` is an
    optional human reason recorded on the activity feed; ``actor`` names who is
    acting (default ``you``, the operator) so an agent-driven transition is
    attributed correctly. POST /api/assets/approve is a thin alias for this with
    ``to="approved"``.
    """

    brand: str
    file: str
    asset: str
    kind: str = "generated"
    to: str
    note: str | None = None
    actor: str = "you"


class TransitionResult(BaseModel):
    """The response from POST /api/assets/transition: the refreshed catalog entry."""

    asset: AssetRecord


# ---------------------------------------------------------------------------
# Activity / provenance feed
# ---------------------------------------------------------------------------


class ActivityEvent(BaseModel):
    """One entry in the append-only activity / provenance feed.

    The orientation record for an act in the Studio, whether a human, an agent,
    or a system did it. ``ts`` is the ISO-8601 instant the server stamped at the
    action boundary. ``actor`` is the doer ("you", "iris", "compliance",
    "asset_pack"); ``actor_kind`` classifies it ("human" | "agent" | "system").
    ``action`` is the verb the feed shows ("approved", "requested changes",
    "generated", "flagged off-brand", "ran asset pack"). ``brand`` is the brand
    slug the act touched (or null), ``target`` the asset it touched (e.g.
    "logo/foo.png", or null), and ``note`` an optional human reason.
    """

    id: str
    ts: str | None = None
    actor: str
    actor_kind: str
    action: str
    brand: str | None = None
    target: str | None = None
    note: str | None = None


class ActivityList(BaseModel):
    """The response from GET /api/activity: events newest-first."""

    events: list[ActivityEvent] = Field(default_factory=list)


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
# Director (Iris): the conversational orchestrator over Studio
# ---------------------------------------------------------------------------


class DirectorTurn(BaseModel):
    """One prior turn in the Director conversation, as the frontend records it.

    ``role`` is ``user`` (the operator) or ``agent`` (Iris). ``text`` is the
    plain message. The recent tail of these is replayed to the model so Iris
    keeps context across a session without the backend holding any state.
    """

    role: str
    text: str


class DirectorChatRequest(BaseModel):
    """Body for POST /api/director/chat.

    ``message`` is the operator's new line. ``brand`` is the active brand slug
    in the Studio, or null when no brand is focused. ``history`` is the recent
    conversation tail (oldest first); it is optional and defaults to empty.
    """

    message: str
    brand: str | None = None
    history: list[DirectorTurn] = Field(default_factory=list)


class DirectorReply(BaseModel):
    """The Director's structured reply (POST /api/director/chat).

    ``reply`` is Iris speaking in first person; the frontend renders it as her
    turn in the transcript. ``intent`` tells the frontend what to do next:

      chat      nothing more; the reply stands on its own.
      single    generate one asset; ``asset``/``variant``/``topic`` scope it
                through the existing prompt-preview/generate flow.
      pack      generate a named pack; ``pack`` is one of the known pack names
                and the frontend calls the asset_pack agent (dry_run first).
      research  research a brand; the frontend calls the brand_research agent.
      agent     create (or surface) an on-brand agent identity for the active
                brand; the route already created it via ``agent_identity`` and
                ``agent`` carries {slug, name, role, brand} so the frontend can
                show the created agent and offer to generate its avatar.
      compliance the operator asked to CHECK existing work against the brand;
                the route already ran the deterministic gate and ``compliance``
                carries the verdict {brand, target, status, score, violations,
                ...} so the frontend renders the result instead of generating.

    ``brand`` is the resolved brand slug (Iris echoes the active one unless the
    operator clearly means another). The asset/variant/pack/topic fields are
    populated only for the intent that uses them and are null otherwise.
    ``agent`` is populated only for the ``agent`` intent: a dict with the keys
    ``slug``, ``name``, ``role``, and ``brand`` of the agent identity the route
    created (or, when it already existed, the one it loaded). It is null for
    every other intent.
    """

    reply: str
    intent: str = "chat"
    brand: str | None = None
    asset: str | None = None
    variant: str | None = None
    pack: str | None = None
    topic: str | None = None
    # Populated only for intent "agent": {slug, name, role, brand} of the agent
    # identity the route created or surfaced. Null for every other intent.
    agent: dict | None = None
    # Populated only for intent "compliance": the deterministic gate's verdict
    # ({brand, target, status, score, violations, counts}). Null otherwise.
    compliance: dict | None = None


# ---------------------------------------------------------------------------
# Compliance: tokens, rules, and the deterministic checker
# ---------------------------------------------------------------------------


class ColorToken(BaseModel):
    """One semantic color token: a role name, its hex, and its RGB triple."""

    name: str
    value: str
    rgb: list[int] = Field(default_factory=list)


class TypographyToken(BaseModel):
    """One typography token: a role name and the font family it resolves to."""

    name: str
    value: str


class BrandTokens(BaseModel):
    """Machine-readable design tokens for a brand (GET /brands/{slug}/tokens).

    Semantic color and typography tokens plus a ready-to-paste ``css`` block of
    ``:root`` custom properties. Only colors the brand actually defines appear.
    """

    brand: str
    color: list[ColorToken] = Field(default_factory=list)
    typography: list[TypographyToken] = Field(default_factory=list)
    css: str = ""


class ManualRule(BaseModel):
    """A human-judged rule recorded on the manifest but not auto-scored."""

    key: str
    note: str


class BrandRules(BaseModel):
    """The deterministic rules manifest for a brand (GET /brands/{slug}/rules)."""

    brand: str
    allowed_colors: list[str] = Field(default_factory=list)
    color_tolerance: int = 28
    gradient: list[str] = Field(default_factory=list)
    banned_terms: list[str] = Field(default_factory=list)
    voice_tone: list[str] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    manual_rules: list[ManualRule] = Field(default_factory=list)


class ComplianceCheckRequest(BaseModel):
    """Body for POST /compliance/check.

    Supply ``text`` to check a piece of copy, or ``file`` (with ``asset`` and
    ``kind``) to check a catalog asset's pixels and its prompt. ``brand`` is
    always required so the checker knows which rules to apply.
    """

    brand: str
    file: str | None = None
    asset: str | None = None
    kind: str | None = None
    text: str | None = None


class OffendingColor(BaseModel):
    """An off-brand color the pixel check flagged, with its area share."""

    hex: str
    proportion: float


class ComplianceCheck(BaseModel):
    """One rule's verdict within a ComplianceResult.

    ``offending``/``found``/``missing`` are populated only by the rule that uses
    them (color_palette / banned_terms / required_terms respectively).
    """

    rule: str
    status: str
    detail: str = ""
    offending: list[OffendingColor] = Field(default_factory=list)
    found: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class ComplianceResult(BaseModel):
    """The verdict for one target: per-rule checks plus an overall status/score."""

    brand: str
    target: str
    status: str
    score: int = 0
    checks: list[ComplianceCheck] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)


class ComplianceRollupSummary(BaseModel):
    """The tallied counts and average score across a brand's assets."""

    total: int = 0
    pass_: int = Field(default=0, alias="pass")
    warn: int = 0
    fail: int = 0
    skipped: int = 0
    score: int = 0

    model_config = {"populate_by_name": True}


class ComplianceAssetRow(BaseModel):
    """One asset's row in the brand-wide compliance rollup."""

    file: str
    asset_type: str
    kind: str
    status: str
    score: int = 0
    violations: list[str] = Field(default_factory=list)


class BrandComplianceRollup(BaseModel):
    """Brand-wide compliance (GET /brands/{slug}/compliance) over every asset."""

    brand: str
    summary: ComplianceRollupSummary
    assets: list[ComplianceAssetRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Brand guidelines (the assembled brand book)
# ---------------------------------------------------------------------------


class GuidelineBrand(BaseModel):
    """The brand's identity header within a BrandGuidelines document."""

    slug: str
    display_name: str
    tagline: str | None = None
    description: str | None = None
    parent: str | None = None
    # The brand's canonical mark as a ready-to-render URL into /api/assets/file,
    # or null when the brand declares no mark (same URL the brand list exposes).
    mark_url: str | None = None


class GuidelineVoice(BaseModel):
    """The voice section of the brand book: tone keywords and phrases to avoid."""

    tone: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)


class GuidelineLogo(BaseModel):
    """The logo section: the canonical mark plus every logo-type asset (lockups)."""

    mark_url: str | None = None
    lockups: list[AssetRecord] = Field(default_factory=list)


class AssetGroup(BaseModel):
    """One asset type's slice of the brand book.

    ``asset_type`` is the registry key, ``label`` its human label from the
    asset-type registry, ``count`` the number of catalog items, and ``items``
    the records themselves in the same AssetRecord shape the catalog serves.
    """

    asset_type: str
    label: str
    count: int = 0
    items: list[AssetRecord] = Field(default_factory=list)


class BrandGuidelines(BaseModel):
    """The full brand book assembled from data we already have.

    One read endpoint (GET /brands/{slug}/guidelines) gathers the identity,
    the design tokens (colors + typography), the deterministic rules manifest,
    the voice, the logo lockups, and every asset grouped by type in registry
    order. Nothing here is computed live beyond reading the same disk catalog
    the asset list reads; ``generated_at`` is left null by the route.
    """

    brand: GuidelineBrand
    colors: list[ColorToken] = Field(default_factory=list)
    typography: list[TypographyToken] = Field(default_factory=list)
    rules: BrandRules
    voice: GuidelineVoice
    logo: GuidelineLogo
    asset_groups: list[AssetGroup] = Field(default_factory=list)
    # The route leaves this null (it does not call a clock); a caller or a later
    # persistence layer can stamp it.
    generated_at: str | None = None


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


# ---------------------------------------------------------------------------
# Agent identities (on-brand identity for an agent: avatar + persona + manifest).
# Distinct from the Brigade-registry AgentInfo/AgentList above: those describe
# runnable agents; these describe an agent's BRANDING, owned by a brand.
# ---------------------------------------------------------------------------


class AgentIdentityOut(BaseModel):
    """One agent identity owned by a brand (GET/POST /brands/{slug}/agents)."""

    slug: str
    brand: str
    name: str
    role: str
    bio: str = ""
    voice_tone: list[str] = Field(default_factory=list)
    avatar_file: str | None = None
    avatar_url: str | None = None
    # The agent's avatar state set: each generated state mapped to its variant
    # URL ("/api/brands/<slug>/agents/<agent>/variants/<state>"). Always includes
    # "primary" once an avatar exists; empty until variants are generated.
    variants: dict[str, str] = Field(default_factory=dict)
    compliance: dict | None = None
    # The governance bucket derived from the avatar's compliance, so a card can
    # badge the agent without re-deriving the rule: "on_brand" (a passing,
    # warning, or skipped avatar), "needs_review" (a compliance fail), or
    # "unrated" (no avatar generated yet).
    governance_status: str = "unrated"
    created_at: str | None = None


class AgentIdentityList(BaseModel):
    agents: list[AgentIdentityOut] = Field(default_factory=list)


class AgentTeamMember(BaseModel):
    """A named, non-created member of a brand's agent team (Director or Steward).

    The Director (Iris) and the per-brand Steward are personas the team view
    shows alongside the brand's created agents but that have no agent.json: just a
    ``name``, a ``role``, a ``kind`` ("director" | "steward"), and an
    ``avatar_url`` (null for the Director; the brand's canonical mark for the
    Steward until a dedicated avatar exists).
    """

    name: str
    role: str
    kind: str
    avatar_url: str | None = None


class AgentTeamGovernance(BaseModel):
    """The team's brand-governance tally over its created agents.

    ``total`` is the number of created agents; each falls into exactly one bucket
    by its avatar compliance — ``on_brand`` (pass/warn/skipped), ``needs_review``
    (a compliance fail), or ``unrated`` (no avatar yet) — and the three counts sum
    to ``total``.
    """

    total: int = 0
    on_brand: int = 0
    needs_review: int = 0
    unrated: int = 0


class AgentTeam(BaseModel):
    """A brand's full agent team (GET /brands/{slug}/agent-team).

    The per-brand team view: the shared ``director`` persona (Iris), this brand's
    ``steward``, the list of created ``agents`` (each in the same
    ``AgentIdentityOut`` shape the agents endpoint serves), and a ``governance``
    rollup over those agents' brand compliance.
    """

    brand: str
    brand_display_name: str
    director: AgentTeamMember
    steward: AgentTeamMember
    agents: list[AgentIdentityOut] = Field(default_factory=list)
    governance: AgentTeamGovernance


class CreateAgentIdentityRequest(BaseModel):
    """Body for POST /brands/{slug}/agents: name + role (+ optional bio)."""

    name: str
    role: str
    bio: str = ""


class MeOut(BaseModel):
    """The authenticated caller, as the GUI shows it (user menu, settings)."""

    uc_uid: str
    email: str | None = None
    name: str | None = None
    # The active workspace for this request. ``workspace_id`` is the canonical
    # uuid; ``workspace`` is its slug (the chip label); ``role`` is the caller's
    # role in it. All None when the caller has no usable membership, or unscoped
    # (local/CLI). ``org`` is kept as a back-compat alias for the slug so the
    # existing user menu / settings rows keep rendering.
    workspace_id: str | None = None
    workspace: str | None = None
    role: str | None = None
    org: str | None = None
    issuer: str | None = None
    anonymous: bool
    scopes: list[str] = Field(default_factory=list)
    logout_url: str | None = None


class WorkspaceOut(BaseModel):
    """One workspace the caller belongs to -- the switcher's row."""

    workspace_id: str
    slug: str
    role: str
    is_default: bool = False


class MeWorkspacesOut(BaseModel):
    """The caller's workspaces (the switcher's data) and the active one."""

    active_workspace_id: str | None = None
    workspaces: list[WorkspaceOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Agent governance + spend (the Crew page's real controls).
# Distinct from the AgentInfo/AgentList (runnable registry) and the
# AgentIdentity* (a brand's agent branding) above: these are an agent's OPERATING
# CONTRACT in a workspace -- its autonomy posture, standing orders, an optional
# weekly budget, a model override -- plus the real metered 7-day spend rolled in.
# ---------------------------------------------------------------------------


class AgentGovernanceOut(BaseModel):
    """One agent's governance + its 7-day spend (GET /api/governance/agents).

    ``agent_kind``/``agent_ref`` identify the agent (e.g. director/iris,
    steward/<brand slug>, specialist/<name>). ``autonomy`` is one of propose,
    auto_with_review, autonomous. ``weekly_budget_cents`` is null when no cap is
    set (v1 displays it; it does not enforce a stop at the cap), ``model`` is null
    when the agent uses its own default. ``spend_cents_7d``/``tokens_7d``/
    ``calls_7d`` are the rolled-up metered spend over the last 7 days.
    ``is_default`` is true when no row is saved yet and these are the synthesized
    defaults, so the GUI can show a subtle "defaults" hint.
    """

    agent_kind: str
    agent_ref: str
    autonomy: str = "propose"
    standing_orders: str = ""
    weekly_budget_cents: int | None = None
    model: str | None = None
    spend_cents_7d: float = 0
    tokens_7d: int = 0
    calls_7d: int = 0
    is_default: bool = True


class AgentGovernanceList(BaseModel):
    """The response from GET /api/governance/agents: every governed agent."""

    governance: list[AgentGovernanceOut] = Field(default_factory=list)


class AgentGovernanceUpdate(BaseModel):
    """Body for PUT /api/governance/agents/{agent_kind}/{agent_ref}.

    Every field is optional: only the ones supplied are changed. ``autonomy``
    must be one of the known levels and ``weekly_budget_cents`` must be zero or
    positive (validated server-side -> 422). A null ``weekly_budget_cents`` or
    ``model`` in the JSON clears that setting (no cap / the agent default); a
    field simply omitted from the body leaves the stored value untouched.
    """

    autonomy: str | None = None
    standing_orders: str | None = None
    weekly_budget_cents: int | None = None
    model: str | None = None
