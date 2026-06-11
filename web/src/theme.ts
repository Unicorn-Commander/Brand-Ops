/**
 * Theme: the fixed Unicorn Commander dark chrome plus the per-brand accent.
 *
 * The app shell (background, surfaces, borders, text) is constant and lives in
 * `chrome` below and in tailwind.config.js. The ACTIVE brand themes the content
 * by setting two CSS vars, `--accent` and `--accent-secondary`, which the
 * Tailwind `accent` color tokens read. Call applyBrandAccent() when the brand
 * switches; pass a known slug for a built-in pairing, or pass explicit colors
 * (the brand's own primary/accent from the API) to theme an arbitrary brand.
 */
import type { BrandColors } from "./lib/types";

/** Fixed dark-chrome tokens. Mirrors the `chrome`/`text` palette in Tailwind. */
export const chrome = {
  bg: "#0a0a0f",
  surface: "#101018",
  panel: "#14141d",
  raised: "#1b1b27",
  border: "#26263a",
  hover: "#1f1f2e",
  text: "#f5f5fa",
  textMuted: "#9a9aae",
  textFaint: "#67677d",
} as const;

/** An accent pair: primary drives `--accent`, secondary drives the gradient. */
export interface AccentPair {
  primary: string;
  secondary: string;
}

/**
 * Built-in accent pairings for the suite brands, keyed by slug. These match the
 * brand identities. A brand not listed here falls back to its own colors from
 * the API (see applyBrandAccent) or to the magic-unicorn default.
 */
export const BRAND_ACCENTS: Record<string, AccentPair> = {
  "magic-unicorn": { primary: "#b66eff", secondary: "#00d4ff" },
  "unicorn-commander": { primary: "#8b5cf6", secondary: "#b66eff" },
  "center-deep": { primary: "#0891b2", secondary: "#22d3ee" },
  majiks: { primary: "#a855f7", secondary: "#eab308" },
  // majiks-studio is the darker child of majiks; same purple/gold identity.
  "majiks-studio": { primary: "#9333ea", secondary: "#eab308" },
};

/** The default accent when no brand is active (magic-unicorn). */
export const DEFAULT_ACCENT: AccentPair = BRAND_ACCENTS["magic-unicorn"];

/**
 * Parse a #rrggbb (or #rgb) hex string into "r g b" space-separated channels,
 * the form the CSS vars use so Tailwind can apply alpha. Returns null on a value
 * it cannot parse, so callers can fall back rather than render a broken color.
 */
export function hexToRgbChannels(hex: string): string | null {
  let h = hex.trim().replace(/^#/, "");
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `${r} ${g} ${b}`;
}

function setAccentVars(pair: AccentPair, root: HTMLElement): void {
  const primary = hexToRgbChannels(pair.primary);
  const secondary = hexToRgbChannels(pair.secondary);
  if (primary) root.style.setProperty("--accent", primary);
  if (secondary) root.style.setProperty("--accent-secondary", secondary);
}

/**
 * Resolve an accent pair for a brand. A known slug uses its built-in pairing.
 * Otherwise, when the brand's own colors are supplied, primary -> accent and
 * accent -> secondary. Falls back to the default when nothing resolves.
 */
export function resolveAccent(
  slugOrColors: string | BrandColors,
): AccentPair {
  if (typeof slugOrColors === "string") {
    return BRAND_ACCENTS[slugOrColors] ?? DEFAULT_ACCENT;
  }
  const { primary, accent } = slugOrColors;
  if (primary && accent) return { primary, secondary: accent };
  return DEFAULT_ACCENT;
}

/**
 * Apply a brand's accent to the document. Pass a slug (built-in pairing) or the
 * brand's BrandColors (themes any brand from its own API values). No-op safe on
 * the server / when document is unavailable.
 */
export function applyBrandAccent(slugOrColors: string | BrandColors): void {
  if (typeof document === "undefined") return;
  const pair = resolveAccent(slugOrColors);
  setAccentVars(pair, document.documentElement);
}

/** Restore the default accent (used when no brand is selected). */
export function resetBrandAccent(): void {
  if (typeof document === "undefined") return;
  setAccentVars(DEFAULT_ACCENT, document.documentElement);
}
