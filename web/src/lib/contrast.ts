/**
 * APCA accent-contrast guard.
 *
 * Customer brands supply arbitrary primary/accent colors. A dark-navy or
 * near-black brand accent renders nearly invisible when used as accent text,
 * icons, or links on our dark chrome background (#0a0a0f). This module measures
 * accent legibility with APCA and, when too dark, nudges the accent lighter so
 * it stays readable on the dark chrome — applied ONLY to arbitrary brand colors
 * (see theme.ts), never to the tuned built-in suite pairings.
 *
 * The contrast function here is APCA-W3 (the Accessible Perceptual Contrast
 * Algorithm, SAPC 0.1.x lineage). It is a PERCEPTUAL estimate of lightness
 * contrast (Lc), NOT WCAG 2 contrast ratio; the two are not interchangeable.
 * The function is kept pure and deterministic so it is unit-testable.
 */

// APCA-W3 / SAPC 0.1.x constants. These are the published magic numbers; do not
// retune them — APCA is calibrated as a whole, and partial edits invalidate it.
const SA98G = {
  // sRGB -> luminance exponent and channel weights (Rec. 709 primaries).
  mainTRC: 2.4,
  sRco: 0.2126,
  sGco: 0.7152,
  sBco: 0.0722,
  // Black soft-clamp: lift very dark luminances so near-blacks don't read as a
  // contrast cliff.
  blkThrs: 0.022,
  blkClmp: 1.414,
  // Polarity-specific power curves (normal = dark text on light bg; reverse =
  // light text on dark bg) and the linear scale + offset to the Lc percentage.
  normBG: 0.56,
  normTXT: 0.57,
  revTXT: 0.62,
  revBG: 0.65,
  scaleBoW: 1.14,
  scaleWoB: 1.14,
  loBoWoffset: 0.027,
  loWoBoffset: 0.027,
  // Below this raw delta the result clamps to 0 (no usable contrast).
  deltaYmin: 0.0005,
  loClip: 0.1,
} as const;

/**
 * Parse a #rrggbb (or #rgb) hex into an [r, g, b] 0..255 triple, or null when
 * the value is unparseable so callers can fall back rather than throw.
 */
function parseHex(hex: string): [number, number, number] | null {
  let h = hex.trim().replace(/^#/, "");
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  if (h.length !== 6 || /[^0-9a-fA-F]/.test(h)) return null;
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/** sRGB 0..255 channel triple -> screen luminance Y, with the black soft-clamp. */
function srgbToY(rgb: [number, number, number]): number {
  const lin = (c: number) => Math.pow(c / 255, SA98G.mainTRC);
  let y =
    SA98G.sRco * lin(rgb[0]) +
    SA98G.sGco * lin(rgb[1]) +
    SA98G.sBco * lin(rgb[2]);
  // Soft-clamp near-black luminance toward the threshold.
  if (y < SA98G.blkThrs) {
    y += Math.pow(SA98G.blkThrs - y, SA98G.blkClmp);
  }
  return y;
}

/**
 * APCA-W3 lightness contrast (Lc) of `text` against `bg`, both #rrggbb.
 *
 * Returns SIGNED Lc roughly in [-108, 106]: positive for dark text on a light
 * background, negative for light text on a dark background. |Lc| ~ 0 means no
 * usable contrast. Returns 0 when either color is unparseable. This is a
 * perceptual estimate (APCA-W3), not a WCAG 2 ratio.
 */
export function apcaContrast(text: string, bg: string): number {
  const txtRgb = parseHex(text);
  const bgRgb = parseHex(bg);
  if (!txtRgb || !bgRgb) return 0;

  const ytxt = srgbToY(txtRgb);
  const ybg = srgbToY(bgRgb);

  // Below the minimum luminance delta there is no meaningful contrast.
  if (Math.abs(ybg - ytxt) < SA98G.deltaYmin) return 0;

  let outputContrast: number;
  if (ybg > ytxt) {
    // Normal polarity: lighter background, darker text -> positive Lc.
    const sapc =
      (Math.pow(ybg, SA98G.normBG) - Math.pow(ytxt, SA98G.normTXT)) *
      SA98G.scaleBoW;
    outputContrast =
      sapc < SA98G.loClip ? 0 : sapc - SA98G.loBoWoffset;
  } else {
    // Reverse polarity: darker background, lighter text -> negative Lc.
    const sapc =
      (Math.pow(ybg, SA98G.revBG) - Math.pow(ytxt, SA98G.revTXT)) *
      SA98G.scaleWoB;
    outputContrast =
      sapc > -SA98G.loClip ? 0 : sapc + SA98G.loWoBoffset;
  }

  return outputContrast * 100;
}

// --- HSL conversion (preserve hue + saturation while raising lightness) -------

function rgbToHsl(
  rgb: [number, number, number],
): [number, number, number] {
  const r = rgb[0] / 255;
  const g = rgb[1] / 255;
  const b = rgb[2] / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l]; // achromatic
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  switch (max) {
    case r:
      h = (g - b) / d + (g < b ? 6 : 0);
      break;
    case g:
      h = (b - r) / d + 2;
      break;
    default:
      h = (r - g) / d + 4;
      break;
  }
  return [h / 6, s, l];
}

function hue2rgb(p: number, q: number, t: number): number {
  let tt = t;
  if (tt < 0) tt += 1;
  if (tt > 1) tt -= 1;
  if (tt < 1 / 6) return p + (q - p) * 6 * tt;
  if (tt < 1 / 2) return q;
  if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
  return p;
}

function hslToHex(h: number, s: number, l: number): string {
  let r: number;
  let g: number;
  let b: number;
  if (s === 0) {
    r = g = b = l; // achromatic
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  const to255 = (c: number) =>
    clamp(Math.round(c * 255), 0, 255)
      .toString(16)
      .padStart(2, "0");
  return `#${to255(r)}${to255(g)}${to255(b)}`;
}

/**
 * Ensure `hex` is legible as an accent on `bgHex`, returning a possibly-lightened
 * hex. If |Lc| against the background already clears `minLc`, the input is
 * returned unchanged. Otherwise the color is converted to HSL and its lightness
 * raised in small steps (preserving hue + saturation) until |Lc| clears `minLc`
 * or lightness hits a ceiling, then the adjusted hex is returned. Unparseable
 * input is returned unchanged.
 *
 * `minLc` defaults to 25 — APCA's "spot / incidental" tier for small accent
 * use (icons, a link, a button tint), NOT body-text grade (APCA puts that
 * ~75–90). 25 is chosen deliberately against the suite's OWN tuned accents on
 * this exact chrome bg: those designer-approved brand primaries measure roughly
 * |Lc| 26–43 here (a vivid accent on near-black is genuinely modest contrast).
 * A floor at 25 sits just under that accepted band, so the guard rescues only
 * accents DARKER than the darkest color the suite itself ships — it lifts a
 * near-invisible navy/near-black (|Lc| ~0–12) up to a legible spot value while
 * leaving a vivid mid-tone customer accent untouched. A higher floor (45/60)
 * would wash out perfectly usable brand colors, which is not the intent: the
 * guard rescues the invisible, it does not override a brand's chosen vibrancy.
 */
export function ensureReadableAccent(
  hex: string,
  bgHex: string,
  minLc = 25,
): string {
  const rgb = parseHex(hex);
  if (!rgb) return hex; // unparseable -> leave untouched

  // Already legible enough: keep the brand's exact accent.
  if (Math.abs(apcaContrast(hex, bgHex)) >= minLc) return hex;

  const [h, s, l0] = rgbToHsl(rgb);
  let l = l0;

  const STEP = 0.04; // +4% lightness per iteration
  const CEILING = 0.92; // don't blow the accent out to near-white
  const MAX_ITERS = 20;

  let candidate = hex;
  for (let i = 0; i < MAX_ITERS && l < CEILING; i += 1) {
    l = Math.min(CEILING, l + STEP);
    candidate = hslToHex(h, s, l);
    if (Math.abs(apcaContrast(candidate, bgHex)) >= minLc) break;
  }
  return candidate;
}
