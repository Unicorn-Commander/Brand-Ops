import { describe, it, expect } from "vitest";
import {
  hexToRgbChannels,
  resolveAccent,
  BRAND_ACCENTS,
  DEFAULT_ACCENT,
  animateAccentTo,
  chrome,
} from "./theme";
import { apcaContrast } from "./lib/contrast";
import type { BrandColors } from "./lib/types";

describe("hexToRgbChannels", () => {
  it("parses a 6-digit hex into space-separated channels", () => {
    expect(hexToRgbChannels("#b66eff")).toBe("182 110 255");
  });

  it("expands a 3-digit hex", () => {
    // #abc -> #aabbcc -> 170 187 204
    expect(hexToRgbChannels("#abc")).toBe("170 187 204");
  });

  it("tolerates a missing leading # and surrounding whitespace", () => {
    expect(hexToRgbChannels("  b66eff ")).toBe("182 110 255");
  });

  it("returns null on unparseable input", () => {
    expect(hexToRgbChannels("#xyz123")).toBeNull();
    expect(hexToRgbChannels("#12")).toBeNull();
    expect(hexToRgbChannels("nope")).toBeNull();
  });
});

describe("resolveAccent", () => {
  it("returns a known slug's built-in pairing unchanged", () => {
    expect(resolveAccent("magic-unicorn")).toEqual(BRAND_ACCENTS["magic-unicorn"]);
  });

  it("falls back to the default accent for an unknown slug", () => {
    expect(resolveAccent("no-such-brand")).toEqual(DEFAULT_ACCENT);
  });

  it("nudges a dark arbitrary primary via the guard and passes secondary through", () => {
    const colors: BrandColors = {
      primary: "#0d1b3a", // near-black navy: invisible on the dark chrome
      accent: "#22d3ee", // bright cyan secondary, left as-is
      background: null,
      foreground: null,
      muted: null,
    };
    const out = resolveAccent(colors);

    // The dark primary was lightened (no longer the input value)...
    expect(out.primary).not.toBe(colors.primary);
    // ...to something more legible on the chrome bg.
    expect(Math.abs(apcaContrast(out.primary, chrome.bg))).toBeGreaterThan(
      Math.abs(apcaContrast(colors.primary, chrome.bg)),
    );
    // The secondary (the brand's accent) is passed through untouched.
    expect(out.secondary).toBe(colors.accent);
  });

  it("leaves an already-legible arbitrary primary untouched", () => {
    const colors: BrandColors = {
      primary: "#b66eff", // already clears the spot floor on the chrome bg
      accent: "#00d4ff",
      background: null,
      foreground: null,
      muted: null,
    };
    const out = resolveAccent(colors);
    expect(out.primary).toBe(colors.primary);
    expect(out.secondary).toBe(colors.accent);
  });
});

describe("animateAccentTo (the channels-format tween)", () => {
  // Regression: the tween's targets come from BRAND_ACCENTS as HEXES, but
  // --accent is a "channels" var. When the parser rejected the hex the engine
  // fell into its snap branch and wrote the raw string, leaving
  // `--accent: #8b5cf6`. Tailwind reads that var as rgb(var(--accent) / alpha),
  // so every accent fill, ring, border, and text color in the app silently
  // resolved to transparent -- including the Library's primary Download button.
  it("lands --accent on r g b channels, never a hex", async () => {
    const root = document.documentElement;
    root.style.setProperty("--accent", "182 110 255");
    animateAccentTo(BRAND_ACCENTS["unicorn-commander"], { duration: 0 });
    // The engine writes each frame from a rAF callback, so let it settle.
    await new Promise((r) => setTimeout(r, 60));
    const landed = root.style.getPropertyValue("--accent").trim();
    expect(landed).not.toContain("#");
    expect(landed).toBe("139 92 246");
  });
});
