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
  AgentInfo,
  AgentList,
  AgentRunResult,
  AssetList,
  AssetRecord,
  AssetType,
  AssetTypeList,
  BrandDetail,
  BrandList,
  BrandSummary,
  GenerateRequest,
  GenerateResult,
  ListAssetsParams,
  NeedsKeyError,
  PromptPreview,
  UploadResult,
} from "../lib/types";

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

  const res = await fetch(url, {
    method,
    signal,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
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

/** GET /api/assets?brand=&asset= -> catalog records from sidecars. */
export async function listAssets(
  params: ListAssetsParams = {},
  signal?: AbortSignal,
): Promise<AssetList["assets"]> {
  const data = await request<AssetList>("/assets", {
    signal,
    query: { brand: params.brand, asset: params.asset },
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

export const api = {
  listBrands,
  getBrand,
  listAssetTypes,
  promptPreview,
  generate,
  listAssets,
  upload,
  listAgents,
  runAgent,
};
