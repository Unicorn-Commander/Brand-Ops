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
}
