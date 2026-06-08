/**
 * Studio agent stub.
 *
 * The real plan-and-generate agent lives behind a backend endpoint that does not
 * exist yet. Until it does, this local stub stands in: it reads a free-text
 * request ("make an OG image for the investor update, dark") plus the current
 * controls, and produces a best-effort {@link GenerateRequest} guess together
 * with a short, human-readable rationale.
 *
 * TODO(agent-endpoint): replace `planFromRequest` with a call to the real agent
 * route once it ships (e.g. POST /api/agent/plan returning a GenerateRequest and
 * a plan transcript). The composed prompt should still flow through
 * api.promptPreview so the preview stays a single source of truth. The call
 * site is marked in ../index.tsx with the same tag.
 */
import type { AssetType, BrandSummary, GenerateRequest } from "../../../lib/types";

/** A single inferred field, with a short reason the user can read. */
interface Inference<T> {
  value: T;
  /** Why the agent picked this. Plain language, no marketing. */
  reason: string;
}

/** The outcome of planning a request against the current options. */
export interface AgentPlan {
  /** The request the stub will send to promptPreview / generate. */
  request: GenerateRequest;
  /** One short line per field the agent reasoned about. */
  rationale: string[];
  /** Fields the agent could not infer and left at the control's value. */
  assumed: string[];
}

/**
 * Phrases that point at an asset type, in priority order. The first asset type
 * whose key is enabled and whose phrases match the request wins.
 */
const ASSET_HINTS: { key: string; phrases: string[] }[] = [
  { key: "og_image", phrases: ["og image", "og-image", "open graph", "social preview", "link preview", "share image"] },
  { key: "podcast_cover", phrases: ["podcast", "episode cover", "cover art", "show art"] },
  { key: "app_icon", phrases: ["app icon", "ios icon", "android icon", "launcher icon"] },
  { key: "social_card", phrases: ["social card", "twitter card", "x card", "linkedin card", "post image", "social post"] },
  { key: "avatar", phrases: ["avatar", "profile picture", "profile pic", "headshot", "pfp"] },
  { key: "logo", phrases: ["logo", "wordmark", "logomark", "brandmark"] },
];

/** Variant keywords the stub recognizes. The brand's own variants win if present. */
const VARIANT_HINTS: { value: string; phrases: string[] }[] = [
  { value: "dark", phrases: ["dark", "night", "black background", "on black"] },
  { value: "light", phrases: ["light", "white background", "on white", "bright"] },
  { value: "transparent", phrases: ["transparent", "no background", "cutout", "knockout"] },
  { value: "mono", phrases: ["mono", "monochrome", "single color", "one color", "grayscale"] },
];

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

/** Match a brand by slug or display name appearing in the request text. */
function inferBrand(
  text: string,
  brands: BrandSummary[],
  fallbackSlug: string | null,
): Inference<string> | null {
  for (const b of brands) {
    const name = normalize(b.display_name);
    const slug = normalize(b.slug).replace(/-/g, " ");
    if (
      (name.length > 2 && text.includes(name)) ||
      (slug.length > 2 && text.includes(slug))
    ) {
      return { value: b.slug, reason: `Brand: ${b.display_name} (named in the request).` };
    }
  }
  if (fallbackSlug) {
    const b = brands.find((x) => x.slug === fallbackSlug);
    return {
      value: fallbackSlug,
      reason: b
        ? `Brand: ${b.display_name} (current selection).`
        : `Brand: ${fallbackSlug} (current selection).`,
    };
  }
  return null;
}

/** Pick an asset type from the request, restricted to the enabled set. */
function inferAsset(
  text: string,
  assetTypes: AssetType[],
  fallbackKey: string | null,
): Inference<string> | null {
  const enabled = new Set(assetTypes.map((a) => a.key));
  for (const hint of ASSET_HINTS) {
    if (!enabled.has(hint.key)) continue;
    if (hint.phrases.some((p) => text.includes(p))) {
      const label = assetTypes.find((a) => a.key === hint.key)?.label ?? hint.key;
      return { value: hint.key, reason: `Asset: ${label} (matched the request).` };
    }
  }
  if (fallbackKey && enabled.has(fallbackKey)) {
    const label = assetTypes.find((a) => a.key === fallbackKey)?.label ?? fallbackKey;
    return { value: fallbackKey, reason: `Asset: ${label} (current selection).` };
  }
  // Last resort: the first enabled asset type.
  const first = assetTypes[0];
  if (first) {
    return { value: first.key, reason: `Asset: ${first.label} (default).` };
  }
  return null;
}

/**
 * Infer a variant. Brand-defined variants (from prompt_assets / reference_roles
 * the caller passes in `knownVariants`) take priority, so the stub never invents
 * a variant the brand does not actually have when it can match one by name.
 */
function inferVariant(
  text: string,
  knownVariants: string[],
  fallback: string | null,
): Inference<string> | null {
  const known = knownVariants.map(normalize);
  // Direct mention of a brand-defined variant by name.
  for (let i = 0; i < knownVariants.length; i += 1) {
    if (known[i].length > 1 && text.includes(known[i])) {
      return { value: knownVariants[i], reason: `Variant: ${knownVariants[i]} (named in the request).` };
    }
  }
  // Generic keyword, only if the brand actually offers it (or offers nothing).
  for (const hint of VARIANT_HINTS) {
    if (hint.phrases.some((p) => text.includes(p))) {
      const offered =
        knownVariants.length === 0 ||
        known.includes(normalize(hint.value));
      if (offered) {
        return { value: hint.value, reason: `Variant: ${hint.value} (matched the request).` };
      }
    }
  }
  if (fallback) {
    return { value: fallback, reason: `Variant: ${fallback} (current selection).` };
  }
  return null;
}

/** Pull a short topic out of the request, e.g. "investor update". */
function inferTopic(text: string, fallback: string | null): Inference<string> | null {
  // "for the investor update", "about Q3 launch", "for our pricing page".
  const m =
    text.match(/(?:for|about|on)\s+(?:the\s+|our\s+|a\s+|an\s+)?([a-z0-9][a-z0-9 \-]{2,40})/);
  if (m) {
    const topic = m[1]
      .replace(/\b(image|icon|logo|cover|card|avatar|graphic|asset)\b.*$/, "")
      .replace(/[,.]/g, "")
      .trim();
    if (topic.length >= 3) {
      return { value: topic, reason: `Topic: "${topic}" (from the request).` };
    }
  }
  if (fallback) {
    return { value: fallback, reason: `Topic: "${fallback}" (from the topic field).` };
  }
  return null;
}

/** Inputs the stub needs to reason about a request. */
export interface PlanInputs {
  /** The free-text request the user typed. */
  text: string;
  brands: BrandSummary[];
  assetTypes: AssetType[];
  /** Variants the active brand actually defines, if known. */
  knownVariants: string[];
  /** Current control values, used as fallbacks when nothing is inferred. */
  controls: {
    brand: string | null;
    asset: string | null;
    variant: string | null;
    topic: string | null;
    platform: string | null;
    backend: string | null;
  };
}

/**
 * Map a free-text request plus the current controls to a best-effort
 * GenerateRequest and a short rationale. Pure and synchronous: the actual
 * prompt composition happens server-side via api.promptPreview using the
 * request this returns.
 *
 * Returns null only when there is not enough configuration to form a request
 * (no brand or no asset types available yet).
 */
export function planFromRequest(inputs: PlanInputs): AgentPlan | null {
  const text = normalize(inputs.text);
  const { controls } = inputs;

  const brand = inferBrand(text, inputs.brands, controls.brand);
  const asset = inferAsset(text, inputs.assetTypes, controls.asset);
  if (!brand || !asset) return null;

  const variant = inferVariant(text, inputs.knownVariants, controls.variant);
  const topic = inferTopic(text, controls.topic);

  const rationale: string[] = [brand.reason, asset.reason];
  const assumed: string[] = [];
  if (variant) rationale.push(variant.reason);
  if (topic) rationale.push(topic.reason);

  if (controls.backend) {
    rationale.push(`Backend: ${controls.backend} (current selection).`);
  } else {
    const def = inputs.assetTypes.find((a) => a.key === asset.value)?.default_backend;
    if (def) assumed.push(`backend defaults to ${def} for this asset type`);
  }

  const request: GenerateRequest = {
    brand: brand.value,
    asset: asset.value,
    variant: variant?.value ?? null,
    topic: topic?.value ?? null,
    platform: controls.platform ?? null,
    backend: controls.backend ?? null,
  };

  return { request, rationale, assumed };
}
