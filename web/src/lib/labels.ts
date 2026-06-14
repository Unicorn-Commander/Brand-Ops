/**
 * Mode-aware label map: audience expertise treated as a LOCALE, not a fork.
 *
 * Each entry has a plain-language string (Simple, the default and the newcomer
 * voice) and an advanced string (the evocative/technical voice power users
 * know). Resolve with `pick(pair, mode)`. Honors the house copy rule: no
 * em-dashes, no marketing-speak. Plain-first is correct even for pros, so where
 * a plain label is simply clearer we use it in both voices.
 */
import type { ViewMode } from "../view-mode";

export interface LabelPair {
  simple: string;
  advanced: string;
}

export function pick(pair: LabelPair, mode: ViewMode): string {
  return mode === "simple" ? pair.simple : pair.advanced;
}

/** Top-nav (chrome). Simple leans plain; Advanced keeps the house names. */
export const NAV_LABELS: Record<string, LabelPair> = {
  "/": { simple: "Brands", advanced: "Portfolio" },
  "/library": { simple: "Assets", advanced: "Library" },
  "/standards": { simple: "Brand rules", advanced: "Standards" },
  "/crew": { simple: "Agents", advanced: "Crew" },
};

/** Brand HQ tabs. */
export const BRAND_NAV_LABELS: Record<string, LabelPair> = {
  overview: { simple: "Overview", advanced: "Overview" },
  mark: { simple: "Logo", advanced: "Mark" },
  book: { simple: "Brand book", advanced: "Book" },
  assets: { simple: "Assets", advanced: "Assets" },
  studio: { simple: "Make", advanced: "Studio" },
  grade: { simple: "Score", advanced: "Grade" },
};

/** The three new-brand journeys, in plain (goal-language) and house voice. */
export const NEW_BRAND_MODES: Record<
  string,
  { title: LabelPair; body: LabelPair }
> = {
  describe: {
    title: { simple: "Start from scratch", advanced: "From nothing" },
    body: {
      simple: "Tell us the name and a sentence. We draft the colors, type, and voice.",
      advanced: "Draft from a name and brief.",
    },
  },
  onboard: {
    title: { simple: "Import from a website", advanced: "From a brand" },
    body: {
      simple: "Paste your site and we pull your logo, colors, and fonts.",
      advanced: "Draft from a website URL and the name as hints.",
    },
  },
  extend: {
    title: { simple: "Add a sub-brand", advanced: "Extend the house" },
    body: {
      simple: "Create a related brand under one you already have.",
      advanced: "Create a related sub-brand under the selected parent.",
    },
  },
};

/** Generation control field labels (the Fine-tune panel). */
export const FIELD_LABELS = {
  brand: { simple: "Brand", advanced: "Brand" },
  asset: { simple: "What to make", advanced: "Asset type" },
  variant: { simple: "Style", advanced: "Variant" },
  topic: { simple: "About", advanced: "Topic" },
  backend: { simple: "Quality", advanced: "Model / backend" },
} satisfies Record<string, LabelPair>;
