/**
 * Wire types for the Brand-Ops REST API.
 *
 * These mirror the Pydantic schemas in
 * src/brand_ops/api/schemas.py EXACTLY. Keep them in sync: a field added or
 * renamed in the API must be reflected here. Fields the API marks Optional are
 * typed `T | null` (FastAPI serializes an unset Optional as JSON null).
 */

// ---------------------------------------------------------------------------
// Brands
// ---------------------------------------------------------------------------

export interface BrandColors {
  primary: string;
  accent: string;
  background: string | null;
  foreground: string | null;
  muted: string | null;
}

export interface BrandTypography {
  primary_font: string;
  display_font: string;
  mono_font: string;
}

export interface BrandVisual {
  colors: BrandColors;
  typography: BrandTypography;
  style_keywords: string[];
}

export interface BrandVoice {
  tone: string[];
  forbidden_phrases: string[];
}

/** Compact form returned by GET /api/brands (the list endpoint). */
export interface BrandSummary {
  slug: string;
  display_name: string;
  tagline: string | null;
  parent: string | null;
  /**
   * The brand's canonical mark as a ready-to-render URL into /api/assets/file,
   * or null when the brand declares no mark. Prefer this over guessing a logo
   * from the catalog.
   */
  mark_url: string | null;
}

/** Full brand returned by GET /api/brands/{slug}. Extends the summary. */
export interface BrandDetail extends BrandSummary {
  description: string | null;
  visual: BrandVisual;
  voice: BrandVoice;
  prompt_assets: string[];
  reference_roles: string[];
}

export interface BrandList {
  brands: BrandSummary[];
}

/**
 * Body for POST /api/brands/{slug}/clone: stand up a new brand from an existing
 * one. `new_slug` is the new brand's slug (lowercase, digits, hyphens; a 422 on
 * a bad value), `display_name` its label. When `as_child` is true (the default)
 * the new brand is a sub-brand that inherits the source's visual + voice via the
 * loader's parent merge, so its own file stays minimal; when false the source's
 * visual + voice are copied in and the new brand stands alone (parent null). The
 * source mark is never copied. Returns the newly created BrandDetail; a 409 when
 * `new_slug` already exists.
 */
export interface CloneBrandRequest {
  new_slug: string;
  display_name: string;
  as_child?: boolean;
}

// ---------------------------------------------------------------------------
// Brand intake (stand up a brand new brand from a name + a few hints)
// ---------------------------------------------------------------------------

/**
 * How the Director should source the draft, mirroring the API's `mode`:
 *   - "describe": draft from the name + the operator's one-line description
 *     (the path for both a distinct new brand and a consistent sub-brand; a
 *     sub-brand is signalled by `parent`, not by a separate mode).
 *   - "onboard": draft from a website URL + the name as hints. Deep live-site
 *     capture needs Brigade connected; without it the Director drafts from the
 *     URL and name and says so in the notes.
 */
export type IntakeMode = "describe" | "onboard";

/**
 * Body for POST /api/brands/intake. Stands up a brand new brand: the Director
 * derives a slug from `name` (lowercase, digits, hyphens), drafts a full
 * identity through the LLM gateway, writes a real brand.yaml, and returns the
 * loaded brand plus the draft it narrated.
 *
 * `name` is required. `description` is an optional one-line brief. `url` is the
 * site to onboard from (mode "onboard"). `parent` sets the new brand's parent
 * slug: when present the draft stays CONSISTENT with the parent (the Director is
 * given the parent's palette + voice as context and keeps a clearly related
 * identity). `mode` defaults to "describe" server-side.
 */
export interface IntakeRequest {
  name: string;
  description?: string | null;
  url?: string | null;
  parent?: string | null;
  mode?: IntakeMode;
}

/**
 * What the Director drafted, for the conversation to narrate. Hex colors, the
 * font stack, the voice tone + style keywords, a tagline, and a 1-3 sentence
 * rationale on the choices. This is the draft the panel renders as Iris
 * speaking; the persisted brand is the `brand` on IntakeResult.
 */
export interface IntakeDraft {
  /**
   * The drafted palette, the same five roles as BrandColors: primary and accent
   * are always set; background, foreground, and muted are `string | null` (the
   * API marks them optional, and a neutral fallback draft may leave them unset).
   */
  palette: {
    primary: string;
    accent: string;
    background: string | null;
    foreground: string | null;
    muted: string | null;
  };
  typography: {
    primary_font: string;
    display_font: string;
    mono_font: string;
  };
  voice_tone: string[];
  style_keywords: string[];
  tagline: string;
  /** 1-3 plain sentences on why the Director made these choices. */
  rationale: string;
}

/**
 * POST /api/brands/intake -> the newly created brand, the draft the Director
 * narrated, and her honest notes (e.g. that live-site capture needs Brigade, or
 * that the LLM gateway was unavailable so the identity is a starting point to
 * refine). `brand` is the same shape as GET /brands/{slug}.
 *
 * The errors callers branch on: a 409 when the derived slug already exists, and
 * a 422 on an invalid/empty name. The route never 500s on an LLM hiccup; it
 * falls back to a neutral on-theme default and says so in `notes`. Each error
 * arrives as an ApiError whose `status` distinguishes them.
 */
export interface IntakeResult {
  brand: BrandDetail;
  draft: IntakeDraft;
  notes: string[];
}

// ---------------------------------------------------------------------------
// Asset types
// ---------------------------------------------------------------------------

/** The 6 asset-type keys the engine can produce. Source: asset_types.py. */
export type AssetTypeKey =
  | "logo"
  | "app_icon"
  | "og_image"
  | "podcast_cover"
  | "social_card"
  | "avatar";

export interface AssetType {
  key: string;
  label: string;
  width: number;
  height: number;
  default_backend: string;
  transparent: boolean;
  notes: string;
}

export interface AssetTypeList {
  asset_types: AssetType[];
}

// ---------------------------------------------------------------------------
// Prompt preview + generate
// ---------------------------------------------------------------------------

/** Body shared by POST /api/prompt-preview (dry run) and POST /api/generate. */
export interface GenerateRequest {
  brand: string;
  asset: string;
  variant?: string | null;
  topic?: string | null;
  platform?: string | null;
  backend?: string | null;
  /** 1..8 inclusive (validated server-side). Live generate only. */
  count?: number;
}

/** Dry-run result from POST /api/prompt-preview: the composed prompt and plan. */
export interface PromptPreview {
  brand: string;
  asset: string;
  variant: string | null;
  backend: string;
  width: number;
  height: number;
  transparent: boolean;
  prompt: string;
  reference_images: string[];
  planned_path: string;
  missing_refs: string[];
  warnings: string[];
}

export interface GeneratedFile {
  path: string;
  catalog_path: string | null;
}

/** Live result from POST /api/generate. */
export interface GenerateResult {
  brand: string;
  asset: string;
  variant: string | null;
  backend: string;
  model: string | null;
  mime: string | null;
  cost_cents: number | null;
  files: GeneratedFile[];
  missing_refs: string[];
  warnings: string[];
}

// ---------------------------------------------------------------------------
// Asset catalog (sidecar reads)
// ---------------------------------------------------------------------------

/**
 * An asset's review lifecycle. The compliance gate stamps "needs_review" (on a
 * fail) or "approved" (on pass/warn/skipped) at creation; the remaining states
 * are operator/agent transitions through POST /api/assets/transition:
 *   - "needs_review": flagged off-brand, or re-flagged for another look.
 *   - "approved": cleared review.
 *   - "changes_requested": sent back with a note for rework.
 *   - "archived": retired from the working set.
 * null means the asset has no review state (e.g. older records never gated).
 */
export type AssetLifecycleStatus =
  | "approved"
  | "needs_review"
  | "changes_requested"
  | "archived";

/**
 * The compliance result stamped onto an AssetRecord, when one is present. A
 * compact form of ComplianceResult carrying just what the card and drawer show:
 * the overall status, the score, and the human-readable violations.
 */
export interface AssetCompliance {
  status: ComplianceStatus;
  score: number;
  violations: string[];
}

/** One catalog entry from GET /api/assets, read from a generated sidecar. */
export interface AssetRecord {
  brand: string;
  asset_type: string;
  variant: string | null;
  backend: string | null;
  model: string | null;
  mime: string | null;
  size: number[];
  transparent: boolean | null;
  cost_cents: number | null;
  created_at: string | null;
  file: string;
  kind?: string;
  url?: string | null;
  prompt: string | null;
  reference_images: string[];
  /** Review state: "approved", "needs_review", or null when never gated. */
  lifecycle_status?: AssetLifecycleStatus | null;
  /** Compliance summary for this asset, when the gate has run; else null. */
  compliance?: AssetCompliance | null;
}

export interface AssetList {
  assets: AssetRecord[];
}

// ---------------------------------------------------------------------------
// Asset upload (drag-and-drop into a brand kit)
// ---------------------------------------------------------------------------

/**
 * Response from POST /api/assets/upload. The new entry arrives in the same
 * AssetRecord shape (and with the same /api/assets/file URL) that GET
 * /api/assets serves, so the Library can append it without a reload.
 */
export interface UploadResult {
  asset: AssetRecord;
}

// ---------------------------------------------------------------------------
// Asset approval (clearing a needs_review asset)
// ---------------------------------------------------------------------------

/**
 * Body for POST /api/assets/approve. Identifies the asset to mark approved by
 * its brand, file basename, asset type, and kind (the same keys the catalog and
 * compliance check use to address an asset).
 */
export interface ApproveAssetRequest {
  brand: string;
  file: string;
  asset: string;
  kind: string;
}

/**
 * Response from POST /api/assets/approve. Returns the updated catalog entry in
 * the same AssetRecord shape GET /api/assets serves (now with lifecycle_status
 * "approved"), so the Library can swap it into its cache without a reload.
 */
export interface ApproveAssetResult {
  asset: AssetRecord;
}

// ---------------------------------------------------------------------------
// Asset lifecycle transition (the general review move)
// ---------------------------------------------------------------------------

/**
 * Body for POST /api/assets/transition. Addresses the asset the same way the
 * catalog, compliance check, and approve endpoint do (brand + file basename +
 * asset type + kind), and moves it to a new lifecycle state. `note` is an
 * optional operator line (the reason, e.g. for "changes_requested"); `actor`
 * names who is acting (defaults server-side to the operator). A bad `to` state
 * is a 422; a missing sidecar is a 404. Approve is a thin alias for to:
 * "approved".
 */
export interface TransitionRequest {
  brand: string;
  file: string;
  asset: string;
  kind: string;
  to: AssetLifecycleStatus;
  note?: string;
  actor?: string;
}

// ---------------------------------------------------------------------------
// Activity / provenance feed (append-only log of who did what)
// ---------------------------------------------------------------------------

/**
 * Who performed an activity event. Drives the row's glyph in the feed:
 *   - "human": a person (operator), e.g. "you".
 *   - "agent": an in-suite agent, e.g. "iris" (the Director) or "asset_pack".
 *   - "system": an automated step, e.g. "compliance" stamping a verdict.
 */
export type ActivityActorKind = "human" | "agent" | "system";

/**
 * One entry in the append-only provenance log (GET /api/activity), the
 * orientation surface for both human and agent actions. The server stamps `id`
 * and `ts`. `actor` is the display name of who acted; `action` is a short past
 * tense phrase (e.g. "approved", "requested changes", "generated", "flagged
 * off-brand", "ran asset pack"). `brand` is the brand slug the event concerns
 * (null when cross-brand or n/a); `target` addresses the asset it touched (e.g.
 * "logo/foo.png", null when none); `note` is the optional operator/agent line.
 */
export interface ActivityEvent {
  id: string;
  /** ISO 8601 timestamp the server stamped when the event was appended. */
  ts: string;
  actor: string;
  actor_kind: ActivityActorKind;
  action: string;
  brand: string | null;
  target: string | null;
  note: string | null;
}

/** GET /api/activity?brand=&limit= -> the matching events, newest first. */
export interface ActivityList {
  events: ActivityEvent[];
}

/** Filters for GET /api/activity. */
export interface ListActivityParams {
  /** Restrict to one brand slug; omit for the cross-brand feed. */
  brand?: string;
  /** Cap the number of events returned (server clamps to its own ceiling). */
  limit?: number;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

/**
 * One agent from GET /api/agents, projected from the Brigade registry. Mirrors
 * AgentInfo in schemas.py. `input_schema` is the agent's JSON-schema input
 * contract (an arbitrary JSON-schema object), so the Studio can render a form
 * and gate the agent by scope and tier.
 */
export interface AgentInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  scopes_required: string[];
  tier_required: string;
}

export interface AgentList {
  agents: AgentInfo[];
}

/**
 * Result of POST /api/agents/{name}/run. Mirrors AgentRunResult in schemas.py.
 * `result` is the agent's own return object, passed through verbatim (its keys
 * vary by agent), and `status` is lifted out of it: agents stamp values such as
 * "ok", "skipped", "research_only", "unparsed", or "error". A "skipped" from a
 * missing LLM gateway still arrives as an HTTP 200.
 */
export interface AgentRunResult {
  agent: string;
  status: string;
  result: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Director chat (Iris)
// ---------------------------------------------------------------------------

/**
 * One prior turn in the Director conversation, sent back so Iris has context.
 * `role` is the speaker: "user" is the operator, "agent" is Iris. Mirrors
 * DirectorTurn in schemas.py.
 */
export interface DirectorTurn {
  role: "user" | "agent";
  text: string;
}

/**
 * Body for POST /api/director/chat. `message` is the operator's new line;
 * `brand` is the active brand slug (or null when no brand is focused); `history`
 * is the recent conversation tail, oldest first. Mirrors DirectorChatRequest.
 */
export interface DirectorChatRequest {
  message: string;
  brand?: string | null;
  history?: DirectorTurn[];
}

/**
 * The intent Iris resolves for the operator's message, telling the Studio what
 * to do next: "chat" stands on its own; "single" generates one asset (through
 * the prompt-preview/generate flow); "pack" generates a named pack (through the
 * asset_pack agent); "research" researches a brand (through brand_research);
 * "agent" creates an on-brand agent identity for the active brand (Iris creates
 * it server-side and returns it in `agent`).
 */
export type DirectorIntent = "chat" | "single" | "pack" | "research" | "agent";

/**
 * The agent identity Iris created (or already had) for the active brand, on a
 * DirectorReply with intent "agent". The compact handle the Studio needs to
 * render the created-agent card and to address the agent for follow-up actions
 * (e.g. generating its avatar): the new agent's `slug`, its `name` and `role`,
 * and the `brand` slug it belongs to. Null on every other intent. Mirrors the
 * `agent` field on DirectorReply in schemas.py.
 */
export interface DirectorAgentRef {
  slug: string;
  name: string;
  role: string;
  brand: string;
}

/**
 * POST /api/director/chat -> the Director's structured reply. `reply` is Iris
 * speaking in first person (rendered as her turn). The asset/variant/pack/topic
 * fields are populated only for the intent that uses them, null otherwise;
 * `agent` is set only on intent "agent" (the identity she created or already
 * had). Mirrors DirectorReply in schemas.py.
 */
export interface DirectorReply {
  reply: string;
  intent: DirectorIntent;
  brand: string | null;
  asset: string | null;
  variant: string | null;
  pack: string | null;
  topic: string | null;
  agent?: DirectorAgentRef | null;
}

// ---------------------------------------------------------------------------
// Design tokens
// ---------------------------------------------------------------------------

/** One color token: a semantic name, its hex value, and the parsed RGB triple. */
export interface ColorToken {
  name: string;
  value: string;
  rgb: number[];
}

/** One typography token: a semantic name mapped to a font family. */
export interface FontToken {
  name: string;
  value: string;
}

/**
 * GET /api/brands/{slug}/tokens -> the brand's design tokens in a portable,
 * tooling-friendly shape. `color` carries only the tokens the brand actually
 * defines, and `css` is a ready-to-copy block of :root custom properties.
 */
export interface BrandTokens {
  brand: string;
  color: ColorToken[];
  typography: FontToken[];
  css: string;
}

// ---------------------------------------------------------------------------
// Compliance
// ---------------------------------------------------------------------------

/** A status shared by the rollup, per-asset rows, and per-check results. */
export type ComplianceStatus = "pass" | "warn" | "fail" | "skipped";

/** One offending color found in an asset, with its share of the image. */
export interface OffendingColor {
  hex: string;
  proportion: number;
}

/**
 * One rule's result within a ComplianceResult. The optional arrays are only
 * present on the rules that produce them (color_palette -> offending, etc.), so
 * the UI reads them defensively.
 */
export interface ComplianceCheck {
  rule: string;
  status: ComplianceStatus;
  detail: string;
  offending?: OffendingColor[];
  found?: string[];
  missing?: string[];
}

/**
 * GET /api/brands/{slug}/rules -> the machine-checkable rules the compliance
 * checker enforces. Mirrors the brand rules surfaced on the brand sheet plus
 * the tolerances and manual rules the check relies on.
 */
export interface BrandRules {
  brand: string;
  allowed_colors: string[];
  color_tolerance: number;
  gradient: string[];
  banned_terms: string[];
  voice_tone: string[];
  required_terms: string[];
  manual_rules: { key: string; note: string }[];
}

/**
 * Body for POST /api/compliance/check. Either a file (with its asset type and
 * kind) or a block of `text` is checked; the fields not relevant to the target
 * are omitted.
 */
export interface ComplianceCheckRequest {
  brand: string;
  file?: string;
  asset?: string;
  kind?: "generated" | "imported";
  text?: string;
}

/** POST /api/compliance/check -> the full result for one target. */
export interface ComplianceResult {
  brand: string;
  target: string;
  status: ComplianceStatus;
  score: number;
  checks: ComplianceCheck[];
  violations: string[];
}

/** One asset row in the brand-wide rollup. */
export interface ComplianceAsset {
  file: string;
  asset_type: string;
  kind: "generated" | "imported";
  status: ComplianceStatus;
  score: number;
  violations: string[];
}

/** The pass/warn/fail tally plus the brand's average score. */
export interface ComplianceSummary {
  total: number;
  pass: number;
  warn: number;
  fail: number;
  skipped: number;
  score: number;
}

/**
 * GET /api/brands/{slug}/compliance -> the result of running the check over
 * every catalog asset of the brand. This is the visible compliance gate: which
 * existing assets are on-brand vs off-brand.
 */
export interface BrandComplianceRollup {
  brand: string;
  summary: ComplianceSummary;
  assets: ComplianceAsset[];
}

// ---------------------------------------------------------------------------
// Brand guidelines (the assembled brand book)
// ---------------------------------------------------------------------------

/**
 * The brand's identity header within a BrandGuidelines document. Just the
 * display fields (no nested visual/voice; the guidelines carry those at the top
 * level). Mirrors GuidelineBrand in schemas.py.
 */
export interface GuidelineBrand {
  slug: string;
  display_name: string;
  tagline: string | null;
  description: string | null;
  parent: string | null;
  /**
   * The brand's canonical mark as a ready-to-render URL into /api/assets/file,
   * or null when the brand declares no mark (same value as BrandSummary.mark_url).
   */
  mark_url: string | null;
}

/**
 * The voice section of the brand book: tone keywords and the phrases to avoid.
 * Mirrors GuidelineVoice in schemas.py (same fields as BrandVoice).
 */
export interface GuidelineVoice {
  tone: string[];
  forbidden_phrases: string[];
}

/**
 * The logo section of the brand book: the canonical mark plus every logo-type
 * asset (the lockups), each in the catalog AssetRecord shape (with a ready URL).
 * Mirrors GuidelineLogo in schemas.py.
 */
export interface GuidelineLogo {
  mark_url: string | null;
  lockups: AssetRecord[];
}

/**
 * One asset type's slice of the brand book. `asset_type` is the registry key,
 * `label` its human label (e.g. "App icon"), `count` the number of catalog
 * items, and `items` the records themselves (each with a ready-to-render `url`).
 * The endpoint includes a type only when the brand has assets of it. Mirrors
 * AssetGroup in schemas.py.
 */
export interface AssetGroup {
  asset_type: string;
  label: string;
  count: number;
  items: AssetRecord[];
}

/**
 * GET /api/brands/{slug}/guidelines -> the brand's full guidelines (the brand
 * book) assembled from data the suite already projects: the identity header,
 * the design tokens (colors + typography, the allowed values assets are checked
 * against), the deterministic rules manifest (clear-space and minimum-size
 * notes, banned terms, voice tone), the voice, the logo lockups, and every
 * asset grouped by type in registry order. Assembled server-side from the same
 * loaders the brand sheet, tokens, and rules endpoints use, so the book never
 * disagrees with the rest of the suite. `generated_at` is left null by the
 * route. Mirrors BrandGuidelines in schemas.py.
 */
export interface BrandGuidelines {
  brand: GuidelineBrand;
  /** Semantic color tokens (role name + hex + rgb). */
  colors: ColorToken[];
  /** Typography tokens (role name + font family). */
  typography: FontToken[];
  rules: BrandRules;
  voice: GuidelineVoice;
  logo: GuidelineLogo;
  /** The brand's assets grouped by type, non-empty groups only, registry order. */
  asset_groups: AssetGroup[];
  /** ISO timestamp the document was assembled (null from the route). */
  generated_at: string | null;
}

// ---------------------------------------------------------------------------
// Template Studio (locked, on-brand templates rendered on a canvas)
// ---------------------------------------------------------------------------

/**
 * One slot in a TemplateDef. A slot is either editable text or the brand mark.
 *
 * Geometry is expressed as fractions of the canvas: `x`/`y` are 0..1 of the
 * canvas width/height (the slot's anchor), and `w` is the slot's max width as a
 * fraction of the canvas width, so a template scales cleanly to any pixel size.
 *
 * Colors and fonts reference brand TOKEN names (never raw hex), so the output is
 * on-brand by construction: `color` is a color-token name like "text/foreground"
 * (resolved against GET /api/brands/{slug}/tokens), and `font` is the role
 * suffix "display" or "body" mapping to the "font/display" / "font/body" token.
 * The renderer is the only consumer of this geometry; see pages/templates/render.ts.
 *
 * Mirrors the Slot model in the backend templates contract. Fields that apply
 * only to text slots (default, max_len, align, font, size, color) are null on a
 * mark slot.
 */
export interface TemplateSlot {
  /** Stable key, unique within the template; used as the editor field id. */
  key: string;
  /** "text" = an editable string; "mark" = the brand logo image. */
  kind: "text" | "mark";
  /** Human label shown in the editor form. */
  label: string;
  /** Default text for a text slot; null for a mark slot. */
  default: string | null;
  /** Character cap for a text slot's input; null when unbounded / a mark. */
  max_len: number | null;
  /** Anchor x, 0..1 fraction of the canvas width. */
  x: number;
  /** Anchor y, 0..1 fraction of the canvas height. */
  y: number;
  /** Max width as a 0..1 fraction of the canvas width (wrap + mark fit width). */
  w: number;
  /** Text alignment (text slots); null for a mark. */
  align: "left" | "center" | "right" | null;
  /** Which brand font token to lock to (text slots): "display" or "body". */
  font: "display" | "body" | null;
  /** Font size in px at full canvas scale (text slots); null for a mark. */
  size: number | null;
  /** Color-token name for the ink (text slots), e.g. "text/foreground". */
  color: string | null;
}

/**
 * A data-driven, locked layout the frontend renders on a canvas using the active
 * brand's tokens. Editing is limited to text slot CONTENT; the layout, fonts,
 * colors, and mark are fixed (on-brand by construction). `width`/`height` are the
 * pixel canvas size. `background` is either the brand gradient (brand/primary ->
 * brand/accent) or a solid fill named by a color token. Mirrors TemplateDef in
 * the backend contract (GET /api/templates).
 */
export interface TemplateDef {
  /** Stable template id; also the upload `variant` when saving a render. */
  id: string;
  /** Human label shown in the picker. */
  label: string;
  /** Catalog asset type the render is saved as, e.g. "og_image" | "social_card". */
  asset_type: string;
  /** Canvas width in px. */
  width: number;
  /** Canvas height in px. */
  height: number;
  /**
   * The background fill. "brand-gradient" draws a linear gradient from
   * brand/primary to brand/accent (`color` is null). "solid" fills with the
   * named color token (`color` is e.g. "surface/background").
   */
  background: {
    kind: "brand-gradient" | "solid";
    color: string | null;
  };
  /** The template's slots (text + the mark), in render order. */
  slots: TemplateSlot[];
}

/** GET /api/templates -> the locked templates the Studio can render. */
export interface TemplateList {
  templates: TemplateDef[];
}

// ---------------------------------------------------------------------------
// Errors + health
// ---------------------------------------------------------------------------

/**
 * The body of the HTTP 422 returned by POST /api/generate when the chosen
 * backend has no credentials. In the response it arrives nested under FastAPI's
 * `detail` key (i.e. `{ detail: NeedsKeyError }`).
 */
export interface NeedsKeyError {
  error: "backend_not_configured";
  backend: string;
  detail: string;
}

export interface HealthOut {
  status: string;
  service: string;
  version: string;
  auth_required: boolean;
}

/** Filters for GET /api/assets. */
export interface ListAssetsParams {
  brand?: string;
  asset?: string;
  /** Lifecycle filter: only approved, or only those needing review. */
  status?: AssetLifecycleStatus;
}

/**
 * An agent's governance verdict, derived server-side from its avatar compliance
 * so a card can badge it without re-deriving:
 *   - "on_brand": the avatar passed (status pass/warn/skipped).
 *   - "needs_review": the avatar failed compliance.
 *   - "unrated": no avatar compliance has run.
 */
export type GovernanceStatus = "on_brand" | "needs_review" | "unrated";

/**
 * An agent identity owned by a brand: the on-brand visual + verbal identity for
 * an agent (a Steward, a Specialist, or a company's own agent). Distinct from
 * the runnable AgentInfo (the Brigade registry). `avatar_url` is a ready-to-render
 * /api path, null until the avatar is generated; `voice_tone` is inherited from
 * the brand. The manifest (GET .../manifest) is what the suite/Brigade consumes.
 */
export interface AgentIdentity {
  slug: string;
  brand: string;
  name: string;
  role: string;
  bio: string;
  voice_tone: string[];
  avatar_file: string | null;
  avatar_url: string | null;
  /**
   * The agent's avatar state set: each generated state mapped to its
   * ready-to-render /api URL ("/api/brands/<slug>/agents/<agent>/variants/<state>").
   * States: primary, thinking, success, alert. Includes "primary" whenever an
   * avatar exists; empty until a variant set is generated.
   */
  variants: Record<string, string>;
  compliance: Record<string, unknown> | null;
  /**
   * The agent's governance verdict, derived server-side from its avatar
   * compliance so a card can badge without re-deriving:
   *   - "on_brand": the avatar passed (status pass/warn/skipped).
   *   - "needs_review": the avatar failed compliance.
   *   - "unrated": no avatar compliance has run.
   */
  governance_status: GovernanceStatus;
  created_at: string | null;
}

// ---------------------------------------------------------------------------
// Agent team (the per-brand agent roster + its governance rollup)
// ---------------------------------------------------------------------------

/**
 * One non-created member of the agent team header (the Director or the
 * Steward), as opposed to the brand's created agents (AgentIdentity). `kind`
 * is "director" (Iris, the orchestrator) or "steward" (the per-brand agent).
 * `avatar_url` is a ready-to-render /api path or null: the Director uses the
 * orb fallback (null), the Steward uses the brand's canonical mark when set.
 * Mirrors AgentTeamMember in schemas.py.
 */
export interface AgentTeamMember {
  name: string;
  role: string;
  kind: string;
  avatar_url: string | null;
}

/**
 * The team's governance rollup: how many of the brand's created agents read as
 * on-brand. `total` is the count of created agents; `on_brand` passed avatar
 * compliance, `needs_review` failed it, `unrated` have no avatar compliance
 * yet. Mirrors AgentTeamGovernance in schemas.py.
 */
export interface AgentTeamGovernance {
  total: number;
  on_brand: number;
  needs_review: number;
  unrated: number;
}

/**
 * GET /api/brands/{slug}/agent-team -> the brand's full agent roster: the
 * Director (Iris) and the per-brand Steward as team members, the created agents
 * as AgentIdentity, and the governance rollup over those agents. 404 -> an
 * ApiError(404) when the brand is unknown. Mirrors AgentTeam in schemas.py.
 */
export interface AgentTeam {
  brand: string;
  brand_display_name: string;
  director: AgentTeamMember;
  steward: AgentTeamMember;
  agents: AgentIdentity[];
  governance: AgentTeamGovernance;
}

/** GET /api/me: the verified caller behind this session. */
export interface Me {
  uc_uid: string;
  email: string | null;
  name: string | null;
  org: string;
  issuer: string | null;
  anonymous: boolean;
  scopes: string[];
  logout_url: string | null;
}
