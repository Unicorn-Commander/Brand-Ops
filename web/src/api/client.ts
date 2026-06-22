/**
 * Typed client for the Brand-Ops REST API.
 *
 * Every call hits a relative `/api/...` path; in dev Vite proxies that to the
 * FastAPI server on localhost:8000, in prod the GUI is served from the same
 * origin as the API. No host is ever baked in here.
 *
 * Errors: a non-2xx becomes an `ApiError` carrying the status and the parsed
 * body. The one case callers must branch on is the backend-needs-a-key 422 from
 * generate(): use `isNeedsKeyError(err)` to detect it and read `err.needsKey`.
 */
import type {
  Me,
  MeWorkspaces,
  OnboardingStatus,
  ProvisionedWorkspace,
  ActivityEvent,
  ActivityList,
  AgentGovernance,
  AgentGovernanceList,
  AgentGovernanceUpdate,
  AgentIdentity,
  AgentInfo,
  AgentList,
  AgentRunResult,
  AgentTeam,
  ApproveAssetRequest,
  ApproveAssetResult,
  AssetList,
  AssetRecord,
  AssetType,
  AssetTypeList,
  BrandComplianceRollup,
  BrandDetail,
  BrandGuidelines,
  BrandList,
  BrandRules,
  BrandSummary,
  BrandTokens,
  CloneBrandRequest,
  ComplianceCheckRequest,
  ComplianceResult,
  DirectorChatRequest,
  DirectorReply,
  GenerateCardRequest,
  GenerateRequest,
  GenerateResult,
  IntakeRequest,
  IntakeResult,
  ListActivityParams,
  ListAssetsParams,
  NeedsKeyError,
  PlanGenerationReply,
  PlanGenerationRequest,
  PromptPreview,
  SharePreview,
  TemplateList,
  TemplateDef,
  TransitionRequest,
  UploadResult,
  VoiceCatalog,
} from "../lib/types";

// ---------------------------------------------------------------------------
// Active workspace (the X-Workspace-Id the GUI sends with every call)
// ---------------------------------------------------------------------------
//
// The workspace switcher sets this; it is persisted so a reload keeps the same
// workspace. The backend treats it as a REQUEST: it honors the workspace only
// when the caller is a member, else falls back to their default -- so a stale
// value here can never lock the user out or leak another tenant. null = send no
// header, letting the backend pick the caller's default workspace.
const _WS_KEY = "brandops.workspace";

let _activeWorkspaceId: string | null =
  typeof localStorage !== "undefined" ? localStorage.getItem(_WS_KEY) : null;

/** The workspace the GUI is currently acting in, or null for the default. */
export function getActiveWorkspaceId(): string | null {
  return _activeWorkspaceId;
}

/** Set (and persist) the active workspace; null clears it back to the default. */
export function setActiveWorkspaceId(id: string | null): void {
  _activeWorkspaceId = id;
  try {
    if (id) localStorage.setItem(_WS_KEY, id);
    else localStorage.removeItem(_WS_KEY);
  } catch {
    // localStorage can throw in private mode; the in-memory value still works.
  }
}

/** Error thrown for any non-2xx response. */
export class ApiError extends Error {
  readonly status: number;
  /** The parsed response body, when JSON; otherwise the raw text. */
  readonly body: unknown;
  /**
   * Set when this is the backend-not-configured 422 from generate(). Lets the
   * Studio screen prompt the user for the missing provider key instead of
   * showing a generic failure.
   */
  readonly needsKey?: NeedsKeyError;

  constructor(
    status: number,
    message: string,
    body: unknown,
    needsKey?: NeedsKeyError,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.needsKey = needsKey;
  }
}

/** Narrowing helper: true when an unknown error is the needs-key 422. */
export function isNeedsKeyError(
  err: unknown,
): err is ApiError & { needsKey: NeedsKeyError } {
  return err instanceof ApiError && err.needsKey !== undefined;
}

function isNeedsKeyBody(value: unknown): value is NeedsKeyError {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as Record<string, unknown>).error === "backend_not_configured" &&
    typeof (value as Record<string, unknown>).backend === "string"
  );
}

async function parseBody(res: Response): Promise<unknown> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }
  try {
    return await res.text();
  } catch {
    return null;
  }
}

/**
 * Pull a human-readable message out of a FastAPI error body. FastAPI nests the
 * payload under `detail`, which is either a string or (for the needs-key case)
 * an object.
 */
function messageFromBody(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (isNeedsKeyBody(detail)) return detail.detail || fallback;
    // FastAPI validation errors: detail is an array of {msg, loc, ...}.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0];
      if (first && typeof first === "object" && "msg" in first) {
        return String((first as { msg: unknown }).msg);
      }
    }
  }
  if (typeof body === "string" && body) return body;
  return fallback;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  query?: Record<string, string | undefined>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, query } = options;

  let url = `/api${path}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== "") params.set(key, value);
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (_activeWorkspaceId) headers["X-Workspace-Id"] = _activeWorkspaceId;

  const res = await fetch(url, {
    method,
    signal,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const parsed = await parseBody(res);
    const detail =
      parsed && typeof parsed === "object" && "detail" in parsed
        ? (parsed as { detail: unknown }).detail
        : undefined;
    const needsKey = isNeedsKeyBody(detail) ? detail : undefined;
    throw new ApiError(
      res.status,
      messageFromBody(parsed, `${method} ${path} failed (${res.status})`),
      parsed,
      needsKey,
    );
  }

  // 204 or empty body: nothing to parse.
  if (res.status === 204) return undefined as T;
  return (await parseBody(res)) as T;
}

// ---------------------------------------------------------------------------
// Public API surface. These are the methods the screen teams call.
// ---------------------------------------------------------------------------

/** GET /api/brands -> the seeded brands in summary form. */
export async function listBrands(signal?: AbortSignal): Promise<BrandSummary[]> {
  const data = await request<BrandList>("/brands", { signal });
  return data.brands;
}

/** GET /api/brands/{slug} -> one brand in full. 404 -> ApiError(404). */
export async function getBrand(
  slug: string,
  signal?: AbortSignal,
): Promise<BrandDetail> {
  return request<BrandDetail>(`/brands/${encodeURIComponent(slug)}`, { signal });
}

/**
 * POST /api/brands/{slug}/clone -> stand up a new brand from an existing one,
 * returning the newly created BrandDetail.
 *
 * With `as_child` true (the default) the new brand is a sub-brand that inherits
 * the source's identity through the loader's parent merge; with it false the
 * source's visual + voice are copied and the new brand stands alone. The source
 * mark is never carried over. The errors callers branch on: a 404 when `slug` is
 * unknown, a 409 when `body.new_slug` already exists, and a 422 on an invalid
 * slug. Each arrives as an ApiError whose `status` distinguishes them.
 */
export async function cloneBrand(
  slug: string,
  body: CloneBrandRequest,
  signal?: AbortSignal,
): Promise<BrandDetail> {
  return request<BrandDetail>(`/brands/${encodeURIComponent(slug)}/clone`, {
    method: "POST",
    body,
    signal,
  });
}

/**
 * POST /api/brands/intake -> stand up a brand new brand from a name + a few
 * hints, returning the created brand, the draft the Director narrated, and her
 * notes.
 *
 * The Director derives a slug from the name, drafts a full identity through the
 * LLM gateway, and writes a real brand.yaml. It never 500s on an LLM hiccup: a
 * gateway miss falls back to a neutral on-theme default and says so in `notes`.
 * The errors callers branch on: a 409 when the derived slug already exists, and
 * a 422 on an invalid/empty name; each arrives as an ApiError whose `status`
 * distinguishes them.
 */
export async function intakeBrand(
  body: IntakeRequest,
  signal?: AbortSignal,
): Promise<IntakeResult> {
  return request<IntakeResult>("/brands/intake", {
    method: "POST",
    body,
    signal,
  });
}

/** GET /api/asset-types -> the asset types the engine can produce. */
export async function listAssetTypes(signal?: AbortSignal): Promise<AssetType[]> {
  const data = await request<AssetTypeList>("/asset-types", { signal });
  return data.asset_types;
}

/** POST /api/prompt-preview -> the composed prompt and plan, no spend. */
export async function promptPreview(
  body: GenerateRequest,
  signal?: AbortSignal,
): Promise<PromptPreview> {
  return request<PromptPreview>("/prompt-preview", {
    method: "POST",
    body,
    signal,
  });
}

/**
 * POST /api/plan-generation -> interpret a free-text request into a concrete
 * GenerateRequest via the backend LLM planner (metered, local model), with a
 * server-side keyword fallback so it always resolves. The Studio free-text box
 * calls this; on a transport failure the caller falls back to the client stub.
 */
export async function planGeneration(
  body: PlanGenerationRequest,
  signal?: AbortSignal,
): Promise<PlanGenerationReply> {
  return request<PlanGenerationReply>("/plan-generation", {
    method: "POST",
    body,
    signal,
  });
}

/**
 * POST /api/generate -> generate live and persist the files.
 *
 * When the backend has no key this rejects with an ApiError whose `needsKey` is
 * set (status 422). Use isNeedsKeyError(err) to detect and recover.
 */
export async function generate(
  body: GenerateRequest,
  signal?: AbortSignal,
): Promise<GenerateResult> {
  return request<GenerateResult>("/generate", {
    method: "POST",
    body,
    signal,
  });
}

/**
 * POST /api/generate-card -> compose an on-brand card deterministically.
 *
 * The keyless path: og_image / social_card rendered from the brand's fonts,
 * colors, and mark with no provider key and no spend. Returns a GenerateResult
 * (same shape as generate). A 422 means the asset has no card layout.
 */
export async function generateCard(
  body: GenerateCardRequest,
  signal?: AbortSignal,
): Promise<GenerateResult> {
  return request<GenerateResult>("/generate-card", {
    method: "POST",
    body,
    signal,
  });
}

/** GET /api/assets?brand=&asset=&status= -> catalog records from sidecars. */
export async function listAssets(
  params: ListAssetsParams = {},
  signal?: AbortSignal,
): Promise<AssetList["assets"]> {
  const data = await request<AssetList>("/assets", {
    signal,
    query: {
      brand: params.brand,
      asset: params.asset,
      status: params.status,
    },
  });
  return data.assets;
}

/**
 * POST /api/assets/upload -> drop one image into a brand kit's imported tree.
 *
 * Sends multipart/form-data with the raw file plus brand, asset_type, and an
 * optional variant. This does NOT go through `request()`: a multipart body must
 * carry the browser-generated boundary, so we must not set Content-Type
 * ourselves. Errors still surface as `ApiError` with the parsed body (an
 * oversized or unsupported file comes back as a 422 whose detail.error is a
 * stable code), so callers handle them exactly like the JSON endpoints.
 *
 * Returns the new catalog record, ready to prepend to the Library list.
 */
export async function upload(
  file: File,
  brand: string,
  assetType: string,
  variant?: string,
  signal?: AbortSignal,
): Promise<AssetRecord> {
  const form = new FormData();
  form.append("file", file);
  form.append("brand", brand);
  form.append("asset_type", assetType);
  if (variant) form.append("variant", variant);

  const res = await fetch("/api/assets/upload", {
    method: "POST",
    headers: _activeWorkspaceId ? { "X-Workspace-Id": _activeWorkspaceId } : undefined,
    body: form,
    signal,
  });

  if (!res.ok) {
    const parsed = await parseBody(res);
    throw new ApiError(
      res.status,
      messageFromBody(parsed, `upload failed (${res.status})`),
      parsed,
    );
  }

  const data = (await parseBody(res)) as UploadResult;
  return data.asset;
}

/**
 * POST /api/assets/transition -> move one asset to a new lifecycle state.
 *
 * The general review move (approve, request changes, archive, re-flag). Writes
 * the new lifecycle_status into the sidecar and appends an activity event, then
 * returns the refreshed catalog record so the Library can swap it into its
 * cache. A bad target state is a 422; a missing sidecar a 404.
 */
export async function transitionAsset(
  body: TransitionRequest,
  signal?: AbortSignal,
): Promise<AssetRecord> {
  const data = await request<ApproveAssetResult>("/assets/transition", {
    method: "POST",
    body,
    signal,
  });
  return data.asset;
}

/**
 * POST /api/assets/approve -> mark one asset as approved.
 *
 * A thin alias for transitionAsset to "approved", kept for the callers that
 * speak the approve verb directly. Addresses the asset by brand + file + asset
 * type + kind, and returns the updated record (now lifecycle_status "approved").
 */
export async function approveAsset(
  body: ApproveAssetRequest,
  signal?: AbortSignal,
): Promise<AssetRecord> {
  return transitionAsset({ ...body, to: "approved" }, signal);
}

/**
 * GET /api/activity?brand=&limit= -> the append-only provenance feed.
 *
 * Returns events newest first. Pass `brand` to scope to one brand, `limit` to
 * cap the count (the server clamps to its own ceiling). This is the orientation
 * surface for both human and agent actions across the suite.
 */
export async function listActivity(
  params: ListActivityParams = {},
  signal?: AbortSignal,
): Promise<ActivityEvent[]> {
  const data = await request<ActivityList>("/activity", {
    signal,
    query: {
      brand: params.brand,
      limit: params.limit !== undefined ? String(params.limit) : undefined,
    },
  });
  return data.events;
}

// ---------------------------------------------------------------------------
// Design tokens + compliance
// ---------------------------------------------------------------------------

/** GET /api/brands/{slug}/tokens -> the brand's portable design tokens. */
export async function getBrandTokens(
  slug: string,
  signal?: AbortSignal,
): Promise<BrandTokens> {
  return request<BrandTokens>(
    `/brands/${encodeURIComponent(slug)}/tokens`,
    { signal },
  );
}

/** GET /api/brands/{slug}/rules -> the machine-checkable brand rules. */
export async function getBrandRules(
  slug: string,
  signal?: AbortSignal,
): Promise<BrandRules> {
  return request<BrandRules>(
    `/brands/${encodeURIComponent(slug)}/rules`,
    { signal },
  );
}

/**
 * GET /api/brands/{slug}/guidelines -> the brand's living guidelines (the brand
 * book): identity, design tokens, the rules manifest, and assets grouped by
 * type, assembled into one document. 404 -> ApiError(404) when the brand is
 * unknown. This is the canonical deliverable the guidelines page renders and the
 * source for the printed PDF export.
 */
export async function getBrandGuidelines(
  slug: string,
  signal?: AbortSignal,
): Promise<BrandGuidelines> {
  return request<BrandGuidelines>(
    `/brands/${encodeURIComponent(slug)}/guidelines`,
    { signal },
  );
}

export async function getSharePreview(
  slug: string,
  opts?: { canonicalUrl?: string; imageUrl?: string },
  signal?: AbortSignal,
): Promise<SharePreview> {
  const params = new URLSearchParams();
  if (opts?.canonicalUrl) params.set("canonical_url", opts.canonicalUrl);
  if (opts?.imageUrl) params.set("image_url", opts.imageUrl);
  const qs = params.toString();
  return request<SharePreview>(
    `/brands/${encodeURIComponent(slug)}/share-preview${qs ? `?${qs}` : ""}`,
    { signal },
  );
}

/** POST /api/compliance/check -> check one file or block of text against a brand. */
export async function checkCompliance(
  body: ComplianceCheckRequest,
  signal?: AbortSignal,
): Promise<ComplianceResult> {
  return request<ComplianceResult>("/compliance/check", {
    method: "POST",
    body,
    signal,
  });
}

/**
 * GET /api/brands/{slug}/compliance -> the rollup of the check run over every
 * catalog asset of the brand. This is the visible on-brand vs off-brand gate.
 */
export async function getBrandCompliance(
  slug: string,
  signal?: AbortSignal,
): Promise<BrandComplianceRollup> {
  return request<BrandComplianceRollup>(
    `/brands/${encodeURIComponent(slug)}/compliance`,
    { signal },
  );
}

// ---------------------------------------------------------------------------
// Templates (locked, on-brand layouts the Template Studio renders)
// ---------------------------------------------------------------------------

/**
 * GET /api/templates -> the locked templates the Template Studio renders.
 *
 * Each TemplateDef is a data-driven layout (canvas size, background, and slots)
 * whose colors and fonts reference brand token names, so a render is on-brand by
 * construction. The frontend draws them on a canvas from the active brand's
 * tokens; the set is static and read-only.
 */
export async function listTemplates(
  signal?: AbortSignal,
): Promise<TemplateDef[]> {
  const data = await request<TemplateList>("/templates", { signal });
  return data.templates;
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

/** GET /api/agents -> the in-module agents the GUI can run. */
export async function listAgents(signal?: AbortSignal): Promise<AgentInfo[]> {
  const data = await request<AgentList>("/agents", { signal });
  return data.agents;
}

/**
 * POST /api/agents/{name}/run -> run one agent against an arbitrary payload.
 *
 * `payload` is the agent's input (its shape is per-agent; see the input_schema
 * from listAgents). An unknown name rejects with ApiError(404). The agents
 * degrade gracefully with no LLM gateway or Brigade configured: the call still
 * resolves 200 with a result whose `status` is something like "skipped", so the
 * caller reads `result.status` rather than treating it as a failure.
 */
export async function runAgent(
  name: string,
  payload: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<AgentRunResult> {
  return request<AgentRunResult>(
    `/agents/${encodeURIComponent(name)}/run`,
    { method: "POST", body: payload, signal },
  );
}

/**
 * POST /api/director/chat -> talk to Iris, the brand director.
 *
 * Iris reads the operator's message (with the active brand and the recent
 * history for context) and returns her first-person reply plus a resolved
 * intent the Studio acts on (chat / single / pack / research). The endpoint
 * degrades in persona: when the LLM gateway is unavailable it still resolves 200
 * with an in-character reply and intent "chat", so the caller treats a thrown
 * error only as a transport failure (and may fall back to the local planner).
 */
export async function directorChat(
  body: DirectorChatRequest,
  signal?: AbortSignal,
): Promise<DirectorReply> {
  return request<DirectorReply>("/director/chat", {
    method: "POST",
    body,
    signal,
  });
}

// ---------------------------------------------------------------------------
// Agent identities (on-brand avatar + persona + manifest, owned by a brand)
// ---------------------------------------------------------------------------

/** GET /api/brands/{slug}/agents -> the brand's agent identities, newest first. */
export async function listBrandAgents(
  slug: string,
  signal?: AbortSignal,
): Promise<AgentIdentity[]> {
  const data = await request<{ agents: AgentIdentity[] }>(
    `/brands/${encodeURIComponent(slug)}/agents`,
    { signal },
  );
  return data.agents;
}

/**
 * GET /api/brands/{slug}/agent-team -> the brand's agent team: the Director
 * (Iris) and the per-brand Steward, the created agents, and the governance
 * rollup over those agents. 404 -> ApiError(404) when the brand is unknown.
 */
export async function getAgentTeam(
  slug: string,
  signal?: AbortSignal,
): Promise<AgentTeam> {
  return request<AgentTeam>(
    `/brands/${encodeURIComponent(slug)}/agent-team`,
    { signal },
  );
}

/** POST /api/brands/{slug}/agents -> create an on-brand agent identity. */
export async function createBrandAgent(
  slug: string,
  body: { name: string; role: string; bio?: string },
  signal?: AbortSignal,
): Promise<AgentIdentity> {
  return request<AgentIdentity>(`/brands/${encodeURIComponent(slug)}/agents`, {
    method: "POST",
    body,
    signal,
  });
}

/** POST /api/brands/{slug}/agents/{agent}/avatar -> generate the agent's avatar. */
export async function generateBrandAgentAvatar(
  slug: string,
  agent: string,
  signal?: AbortSignal,
): Promise<AgentIdentity> {
  return request<AgentIdentity>(
    `/brands/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agent)}/avatar`,
    { method: "POST", signal },
  );
}

/**
 * POST /api/brands/{slug}/agents/{agent}/variants -> generate the agent's full
 * avatar state set (primary, thinking, success, alert), filling in the primary
 * avatar too if missing.
 *
 * Returns the refreshed AgentIdentity, now carrying `variants` (each state mapped
 * to its /api URL). Degrades like the avatar route: a 422 when the backend has no
 * key (its `needsKey` is set), a 502 on a backend error, a 404 on an unknown
 * agent.
 */
export async function generateAgentVariants(
  slug: string,
  agent: string,
  signal?: AbortSignal,
): Promise<AgentIdentity> {
  return request<AgentIdentity>(
    `/brands/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agent)}/variants`,
    { method: "POST", signal },
  );
}

/** The /api URL of an agent's consumable identity manifest (for download/links). */
export function agentManifestUrl(slug: string, agent: string): string {
  return `/api/brands/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agent)}/manifest`;
}

/** GET /api/voices -> the assignable voice catalog and whether preview is wired. */
export async function listVoices(signal?: AbortSignal): Promise<VoiceCatalog> {
  return request<VoiceCatalog>("/voices", { signal });
}

/** PUT /api/brands/{slug}/agents/{agent}/voice -> bind or clear the agent's voice. */
export async function setBrandAgentVoice(
  slug: string,
  agent: string,
  body: { voice_id: string | null; engine?: string | null; speed?: number },
  signal?: AbortSignal,
): Promise<AgentIdentity> {
  return request<AgentIdentity>(
    `/brands/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agent)}/voice`,
    { method: "PUT", body, signal },
  );
}

/** PATCH /api/brands/{slug}/agents/{agent} -> edit the agent's role and/or bio. */
export async function updateBrandAgent(
  slug: string,
  agent: string,
  body: { role?: string; bio?: string },
  signal?: AbortSignal,
): Promise<AgentIdentity> {
  return request<AgentIdentity>(
    `/brands/${encodeURIComponent(slug)}/agents/${encodeURIComponent(agent)}`,
    { method: "PATCH", body, signal },
  );
}

/**
 * POST /api/voices/preview -> synthesize one short sample utterance and return it
 * as an audio Blob (for an Audio() to play). Best-effort: a 503 means preview is
 * not wired (no orator), surfaced as an ApiError the caller can choose to ignore.
 */
export async function previewVoice(
  body: { voice_id: string; text?: string; speed?: number },
  signal?: AbortSignal,
): Promise<Blob> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (_activeWorkspaceId) headers["X-Workspace-Id"] = _activeWorkspaceId;
  const res = await fetch("/api/voices/preview", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const parsed = await parseBody(res);
    throw new ApiError(
      res.status,
      messageFromBody(parsed, `voice preview failed (${res.status})`),
      parsed,
    );
  }
  return res.blob();
}

/** The verified principal behind this session (drives the user menu). */
export async function me(signal?: AbortSignal): Promise<Me> {
  return request<Me>("/me", { signal });
}

/** GET /api/me/workspaces -> the caller's workspaces and the active one. */
export async function meWorkspaces(signal?: AbortSignal): Promise<MeWorkspaces> {
  return request<MeWorkspaces>("/me/workspaces", { signal });
}

/**
 * GET /api/onboarding -> whether self-serve signup is on and whether this caller
 * needs it (authenticated, no workspace yet). Drives the first-run gate.
 */
export async function getOnboardingStatus(
  signal?: AbortSignal,
): Promise<OnboardingStatus> {
  return request<OnboardingStatus>("/onboarding", { signal });
}

/**
 * POST /api/onboarding/provision -> mint (or return) the caller's own workspace.
 * `displayName` is the optional label the user picked; the backend derives one
 * from their identity when it is blank. Idempotent: a caller who already has a
 * workspace gets it back with `created=false`.
 */
export async function provisionWorkspace(
  displayName?: string,
  signal?: AbortSignal,
): Promise<ProvisionedWorkspace> {
  const body = displayName?.trim() ? { display_name: displayName.trim() } : {};
  return request<ProvisionedWorkspace>("/onboarding/provision", {
    method: "POST",
    body,
    signal,
  });
}

// ---------------------------------------------------------------------------
// Agent governance + spend (the Crew page's real controls)
// ---------------------------------------------------------------------------

/**
 * GET /api/governance/agents -> every governed agent (the Director, each brand's
 * Steward, every specialist) with its operating contract and its real 7-day
 * spend. An unscoped caller (no workspace) gets an empty list. Returns the
 * `governance` array directly.
 */
export async function listAgentGovernance(
  signal?: AbortSignal,
): Promise<AgentGovernance[]> {
  const data = await request<AgentGovernanceList>("/governance/agents", { signal });
  return data.governance;
}

/**
 * PUT /api/governance/agents/{agent_kind}/{agent_ref} -> set one agent's
 * operating contract (autonomy, standing orders, weekly budget, model override),
 * returning it with its 7-day spend. `agentRef` may contain a slash (an identity
 * ref), so each path segment is encoded and the slashes are preserved. A 403
 * arrives when the caller has no workspace; a 422 on an invalid autonomy or a
 * negative budget.
 */
export async function putAgentGovernance(
  agentKind: string,
  agentRef: string,
  body: AgentGovernanceUpdate,
  signal?: AbortSignal,
): Promise<AgentGovernance> {
  const ref = agentRef.split("/").map(encodeURIComponent).join("/");
  return request<AgentGovernance>(
    `/governance/agents/${encodeURIComponent(agentKind)}/${ref}`,
    { method: "PUT", body, signal },
  );
}

export const api = {
  me,
  meWorkspaces,
  getOnboardingStatus,
  provisionWorkspace,
  listBrands,
  getBrand,
  cloneBrand,
  intakeBrand,
  listAssetTypes,
  promptPreview,
  planGeneration,
  generate,
  generateCard,
  listAssets,
  upload,
  transitionAsset,
  approveAsset,
  listActivity,
  getBrandTokens,
  getBrandRules,
  getBrandGuidelines,
  checkCompliance,
  getBrandCompliance,
  listTemplates,
  listAgents,
  runAgent,
  directorChat,
  listBrandAgents,
  getAgentTeam,
  createBrandAgent,
  generateBrandAgentAvatar,
  generateAgentVariants,
  agentManifestUrl,
  listAgentGovernance,
  putAgentGovernance,
};
