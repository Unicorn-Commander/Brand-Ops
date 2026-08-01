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
import { ensureReadableAccent } from "./lib/contrast";

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
  // contact-ops is the emerald/mint *-Ops sibling (the one open lane in a suite
  // of purple, cyan, navy, and gold). Without this entry it would fall to the
  // default purple, mismatching its own --b-* brand surfaces.
  "contact-ops": { primary: "#19c37d", secondary: "#5ef2b3" },
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

// ---------------------------------------------------------------------------
// Animated recolor (the brand-switch tween)
// ---------------------------------------------------------------------------
//
// When the active brand changes the accent should TWEEN, not snap, so the whole
// UI reads as gracefully recoloring. A CSS `transition: --accent` is a no-op
// here: these custom properties are not registered with @property, and --accent
// holds "r g b" channel strings (not a parseable <color>), so there is nothing
// the engine can interpolate. We therefore drive the interpolation in JS with
// requestAnimationFrame, writing each frame back to the var.
//
// animateColorVars below is the one shared engine. It also themes EnvProvider's
// visible color vars (--b-primary etc.), which are likewise unregistered and so
// equally un-CSS-transitionable; it is exported for that reuse.

/** ~280ms reads as a smooth recolor without feeling sluggish on a frequent action. */
const ACCENT_TWEEN_MS = 280;

/** easeOutCubic — fast start, gentle settle. Matches the app's ease-out feel. */
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/** True when the OS asks for reduced motion (SSR-safe; assumes motion off-DOM). */
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// The two CSS-var value FORMATS we tween. The interpolation is always over RGBA
// numerically; the format only changes how a frame is parsed and written back:
//   - "channels": "r g b" space-separated (system #1, --accent etc.; Tailwind
//     reads it as rgb(var(--accent) / <alpha>), so it carries no alpha).
//   - "rgb-or-rgba": a hex (#rrggbb, written back as hex) or an rgba(r,g,b,a)
//     string (written back as rgba, alpha interpolated too) — EnvProvider's
//     --b-primary/--b-accent (hex) and --b-bg-glow1/2 (rgba) vars.
export type ChannelVarFormat = "channels" | "rgb-or-rgba";

interface Rgba {
  r: number;
  g: number;
  b: number;
  a: number; // 1 for opaque/hex/channel values
}

/** Parse a "r g b" channel string into an opaque Rgba, or null if unreadable. */
function parseChannels(value: string): Rgba | null {
  const parts = value.trim().split(/\s+/).map(Number);
  if (parts.length !== 3 || parts.some((n) => !Number.isFinite(n))) return null;
  return { r: parts[0], g: parts[1], b: parts[2], a: 1 };
}

/** Parse a #rrggbb/#rgb hex or an rgb()/rgba() string into Rgba, or null. */
function parseRgbOrRgba(value: string): Rgba | null {
  const v = value.trim();
  if (v.startsWith("#")) {
    const channels = hexToRgbChannels(v);
    return channels ? parseChannels(channels) : null;
  }
  const m = v.match(
    /^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*(?:,\s*([0-9.]+)\s*)?\)$/i,
  );
  if (!m) return null;
  const r = Number(m[1]);
  const g = Number(m[2]);
  const b = Number(m[3]);
  const a = m[4] === undefined ? 1 : Number(m[4]);
  if ([r, g, b, a].some((n) => !Number.isFinite(n))) return null;
  return { r, g, b, a };
}

/** Write an Rgba back out in the requested format. */
function formatRgba(c: Rgba, format: ChannelVarFormat): string {
  const r = Math.round(c.r);
  const g = Math.round(c.g);
  const b = Math.round(c.b);
  if (format === "channels") return `${r} ${g} ${b}`;
  // rgb-or-rgba: keep an alpha if the value carries one (a glow), else emit
  // hex (a solid --b-primary/--b-accent).
  if (c.a >= 1) {
    const hex = (n: number) => n.toString(16).padStart(2, "0");
    return `#${hex(r)}${hex(g)}${hex(b)}`;
  }
  // Round alpha to a sane precision so the var doesn't churn long decimals.
  return `rgba(${r}, ${g}, ${b}, ${Math.round(c.a * 1000) / 1000})`;
}

/**
 * One CSS custom property to tween: its name, the target VALUE it should land
 * on (a hex, an rgb()/rgba() string, or — for "channels" — anything
 * hexToRgbChannels accepts), and the value FORMAT (defaults to "channels"). A
 * single animateColorVars call drives a batch of these off one rAF loop so the
 * vars stay phase-locked.
 */
export interface ColorVarTarget {
  /** The custom property name, e.g. "--accent" or "--b-primary". */
  prop: string;
  /** The destination value (hex, or rgb()/rgba() for "rgb-or-rgba"). */
  to: string;
  /** How to parse the current value and write each frame. Default "channels". */
  format?: ChannelVarFormat;
}

// In-flight tween rAF ids, keyed by GROUP. A group is one independent set of
// vars on the shared document root: "accent" (theme.ts's --accent pair) and
// "env" (EnvProvider's --b-* color vars) run as SEPARATE tweens that overlap on
// a brand switch (the env detail re-fetches after the slug changes). Keying by
// group lets a new switch cancel the SAME group's running loop — so rapid
// brand-hopping never stacks loops or leaves a half-blended color — without one
// system stomping the other's in-flight tween.
type TweenGroup = "accent" | "env";
const activeTweenRaf: Map<TweenGroup, number> = new Map();

/**
 * Cancel an in-flight color tween for one group (default "accent"). Called at
 * the start of every new tween in that group so rapid switching never stacks
 * rAF loops or leaves a half-blended color.
 */
export function cancelAccentTween(group: TweenGroup = "accent"): void {
  const id = activeTweenRaf.get(group);
  if (id !== undefined && typeof cancelAnimationFrame !== "undefined") {
    cancelAnimationFrame(id);
  }
  activeTweenRaf.delete(group);
}

/**
 * Tween a batch of color custom properties to their targets over ~280ms with an
 * ease-out curve, writing each frame back in each var's native format. This is
 * the one tween engine both accent systems share (theme.ts's --accent and
 * EnvProvider's --b-* color vars), because none of these vars is registered
 * with @property, so a CSS transition can't interpolate them.
 *
 * Behaviour:
 *   - Reads the CURRENT value off getComputedStyle as the start; any target
 *     whose start is unreadable or whose target won't parse is SNAPPED to its
 *     end value rather than animated.
 *   - Cancels any in-flight tween first, so only one loop ever runs.
 *   - SNAPS instantly (no rAF) when the OS asks for reduced motion.
 *   - SSR-safe: no-ops when document is unavailable.
 *
 * @param targets the vars to drive and the values to land on
 * @param opts.duration override the tween length in ms (defaults to 280)
 * @param opts.group which independent tween group this belongs to (default
 *   "accent"); a new tween cancels only its own group's running loop
 */
export function animateColorVars(
  targets: ColorVarTarget[],
  opts?: { duration?: number; group?: TweenGroup },
): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const group = opts?.group ?? "accent";

  // A new tween supersedes the previous one in the SAME group only.
  cancelAccentTween(group);

  // Resolve each target's start (current value) and end. When the start can't
  // be read or the target won't parse, mark it snap-only (from = null).
  const computed = getComputedStyle(root);
  interface Resolved {
    prop: string;
    format: ChannelVarFormat;
    from: Rgba | null;
    to: Rgba;
  }
  // A "channels" var reads back as "r g b", but a CALLER may hand its target in
  // any color form -- animateAccentTo passes the BRAND_ACCENTS hexes straight
  // through. Accepting both matters: an unparseable target falls into the snap
  // branch below, which writes the raw string, and `--accent: #8b5cf6` makes
  // every Tailwind accent utility (rgb(var(--accent) / <alpha>)) resolve to
  // transparent. That silently emptied every accent fill, ring, and border in
  // the app after the first brand switch.
  const parse = (value: string, format: ChannelVarFormat) =>
    format === "channels"
      ? (parseChannels(value) ?? parseRgbOrRgba(value))
      : parseRgbOrRgba(value);

  const resolved: Resolved[] = [];
  for (const t of targets) {
    const format = t.format ?? "channels";
    const to = parse(t.to, format);
    if (!to) {
      // Unparseable target: can't interpolate, but still honour the write so the
      // var lands on the requested value (don't leave it stale). Snap it now.
      root.style.setProperty(t.prop, t.to);
      continue;
    }
    const from = parse(computed.getPropertyValue(t.prop), format);
    resolved.push({ prop: t.prop, format, from, to });
  }
  if (resolved.length === 0) return;

  const writeEnd = () => {
    for (const r of resolved) {
      root.style.setProperty(r.prop, formatRgba(r.to, r.format));
    }
  };

  // Reduced motion, or every target is snap-only: jump straight to the end.
  if (prefersReducedMotion() || resolved.every((r) => r.from === null)) {
    writeEnd();
    return;
  }

  const start = performance.now();
  const tick = (now: number) => {
    const elapsed = now - start;
    const duration = opts?.duration ?? ACCENT_TWEEN_MS;
    const t = duration <= 0 ? 1 : Math.min(1, elapsed / duration);
    const e = easeOutCubic(t);
    for (const r of resolved) {
      // A target with no readable start can't be interpolated; hold it at its
      // end value for the whole tween.
      const from = r.from ?? r.to;
      const lerp = (a: number, b: number) => a + (b - a) * e;
      root.style.setProperty(
        r.prop,
        formatRgba(
          {
            r: lerp(from.r, r.to.r),
            g: lerp(from.g, r.to.g),
            b: lerp(from.b, r.to.b),
            a: lerp(from.a, r.to.a),
          },
          r.format,
        ),
      );
    }
    if (t < 1) {
      activeTweenRaf.set(group, requestAnimationFrame(tick));
    } else {
      activeTweenRaf.delete(group);
      writeEnd(); // land exactly on the target values
    }
  };
  activeTweenRaf.set(group, requestAnimationFrame(tick));
}

/**
 * Tween the primary app accent (--accent + --accent-secondary) to `pair` over
 * ~280ms. Reuses animateColorVars, so it cancels any in-flight tween, reads the
 * live start off the document, and snaps under reduced motion. Used by the brand
 * switch; first paint uses the instant applyBrandAccent (see below).
 */
export function animateAccentTo(
  pair: AccentPair,
  opts?: { duration?: number },
): void {
  animateColorVars(
    [
      { prop: "--accent", to: pair.primary },
      { prop: "--accent-secondary", to: pair.secondary },
    ],
    opts,
  );
}

/**
 * Resolve an accent pair for a brand. A known slug uses its built-in pairing.
 * Otherwise, when the brand's own colors are supplied, primary -> accent and
 * accent -> secondary. Falls back to the default when nothing resolves.
 *
 * Legibility guard: for an ARBITRARY brand (BrandColors, not a known slug) the
 * resolved primary is passed through ensureReadableAccent against the dark
 * chrome bg, so a near-black customer accent gets nudged lighter rather than
 * rendering invisible as accent text/icons/links. The secondary is left as-is
 * (it drives gradients and can be darker). The built-in suite pairings in
 * BRAND_ACCENTS are already tuned and are deliberately EXEMPT from this guard.
 */
export function resolveAccent(
  slugOrColors: string | BrandColors,
): AccentPair {
  if (typeof slugOrColors === "string") {
    return BRAND_ACCENTS[slugOrColors] ?? DEFAULT_ACCENT;
  }
  const { primary, accent } = slugOrColors;
  if (primary && accent) {
    return { primary: ensureReadableAccent(primary, chrome.bg), secondary: accent };
  }
  return DEFAULT_ACCENT;
}

// True once an accent has been applied this session. The first application
// (initial page load) SNAPS so there is no default->brand recolor flash; every
// subsequent switch tweens. animate:false forces a snap regardless (e.g. resets).
let hasAppliedOnce = false;

/**
 * Apply a brand's accent to the document. Pass a slug (built-in pairing) or the
 * brand's BrandColors (themes any brand from its own API values).
 *
 * The FIRST call of the session snaps (instant), so the initial paint has no
 * recolor flash; later calls TWEEN the recolor over ~280ms. Pass
 * `{ animate: false }` to force an instant apply. Reduced motion always snaps
 * (handled inside the tween). No-op safe on the server / when document is
 * unavailable.
 */
export function applyBrandAccent(
  slugOrColors: string | BrandColors,
  opts?: { animate?: boolean },
): void {
  if (typeof document === "undefined") return;
  const pair = resolveAccent(slugOrColors);
  // Snap on the very first application, or when explicitly told not to animate.
  const animate = (opts?.animate ?? true) && hasAppliedOnce;
  if (animate) {
    animateAccentTo(pair);
  } else {
    cancelAccentTween(); // a snap supersedes any running tween
    setAccentVars(pair, document.documentElement);
  }
  hasAppliedOnce = true;
}

/** Restore the default accent (used when no brand is selected). Snaps. */
export function resetBrandAccent(): void {
  if (typeof document === "undefined") return;
  cancelAccentTween(); // supersede any running tween
  setAccentVars(DEFAULT_ACCENT, document.documentElement);
}
