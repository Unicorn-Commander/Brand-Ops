/**
 * Studio deep links: turning the query string the rest of the app hands us into
 * something the Studio actually acts on.
 *
 * Ten buttons across the app navigated here with a query parameter the Studio
 * never read, so they all did the same nothing — they opened a generic Studio:
 *
 *   Brand HQ    "Discuss ↗" on Color / Typography / Voice   ?context=color|type|voice
 *   Brand HQ    "Discuss the brand"                          ?context=overview
 *   Brand HQ    "Generate →" on every missing kit cell       ?asset=<asset_type>
 *   Mark page   "Ask why it looks this way"                  ?context=mark
 *   Mark page   "Commission variant"                         ?asset=logo
 *   Portfolio   "Generate a kit"                             ?pack=core
 *
 * Only `?prompt=` was wired. This module maps the rest onto the SAME opening
 * turn the front door already uses, plus a control preselect where the parameter
 * names one — no new mechanism, and nothing here invents a backend call.
 *
 * Kept pure (string in, plain object out) so the mapping is unit-testable
 * without mounting the Studio.
 */

/** The topics a `?context=` deep link can ask Iris about. */
const CONTEXT_PROMPTS: Record<string, string> = {
  overview: "Walk me through this brand: what it is, who it is for, and how it should look and sound.",
  color: "Explain this brand's color palette. What is each token for, and where should it never be used?",
  type: "Explain this brand's typography. Which family carries display, which carries body, and why?",
  voice: "Explain this brand's voice: the tone it holds, and the phrases it never uses.",
  mark: "Explain why this brand's mark looks the way it does, and what must stay constant across uses.",
};

export interface StudioDeepLink {
  /** The opening turn to send, or null when the link carries no intent. */
  prompt: string | null;
  /** An asset type to preselect in the controls, or null. */
  asset: string | null;
  /** A pack slug the caller asked to generate, or null. */
  pack: string | null;
}

/**
 * Read a Studio deep link out of a query string.
 *
 * `?prompt=` wins outright (it is the operator's own words). Otherwise a
 * `?context=`, `?asset=` or `?pack=` is translated into a plain-language opening
 * turn. An unrecognized `context` yields no prompt rather than a made-up one.
 */
export function readStudioDeepLink(search: string): StudioDeepLink {
  const params = new URLSearchParams(search);
  const typed = params.get("prompt")?.trim();
  const asset = params.get("asset")?.trim() || null;
  const pack = params.get("pack")?.trim() || null;
  const context = params.get("context")?.trim().toLowerCase() || null;

  if (typed) return { prompt: typed, asset, pack };
  if (context) return { prompt: CONTEXT_PROMPTS[context] ?? null, asset, pack };
  if (asset) {
    return {
      prompt: `Make a ${asset.replace(/_/g, " ")} for this brand.`,
      asset,
      pack,
    };
  }
  if (pack) {
    return { prompt: `Generate the ${pack} pack for this brand.`, asset, pack };
  }
  return { prompt: null, asset, pack };
}

/** The parameters this module consumes, stripped from a URL after it acts. */
export const DEEP_LINK_PARAMS = ["prompt", "context", "asset", "pack"] as const;

/**
 * The same query string with every consumed parameter removed, so a refresh
 * does not resend the opening turn. Returns "" when nothing is left.
 */
export function strippedSearch(search: string): string {
  const params = new URLSearchParams(search);
  for (const key of DEEP_LINK_PARAMS) params.delete(key);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}
