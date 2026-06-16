import { describe, it, expect } from "vitest";
import { apcaContrast, ensureReadableAccent } from "./contrast";

// The dark chrome background the accent guard measures against.
const BG = "#0a0a0f";

describe("apcaContrast", () => {
  it("is negative for light text on a dark background (reverse polarity)", () => {
    // White on near-black: light-on-dark is the reverse-polarity case, which the
    // module documents as a NEGATIVE Lc.
    expect(apcaContrast("#ffffff", BG)).toBeLessThan(0);
  });

  it("is positive for dark text on a light background (normal polarity)", () => {
    expect(apcaContrast("#000000", "#ffffff")).toBeGreaterThan(0);
  });

  it("gives a bright accent a larger |Lc| on dark bg than a near-black one", () => {
    // A vivid color stands off the dark chrome; a near-black barely does. Compare
    // magnitudes, since both are light-on-dark and thus negative.
    const bright = Math.abs(apcaContrast("#b66eff", BG));
    const nearBlack = Math.abs(apcaContrast("#0d1b3a", BG));
    expect(bright).toBeGreaterThan(nearBlack);
  });

  it("returns 0 when either color is unparseable", () => {
    expect(apcaContrast("not-a-color", BG)).toBe(0);
    expect(apcaContrast("#b66eff", "nope")).toBe(0);
  });

  it("returns 0 for two identical colors (no luminance delta)", () => {
    expect(apcaContrast(BG, BG)).toBe(0);
  });
});

describe("ensureReadableAccent", () => {
  it("lightens a near-black navy that is invisible on the dark chrome", () => {
    const input = "#0d1b3a";
    const out = ensureReadableAccent(input, BG);
    // It changed the color...
    expect(out).not.toBe(input);
    // ...and the change increased its contrast magnitude against the bg.
    expect(Math.abs(apcaContrast(out, BG))).toBeGreaterThan(
      Math.abs(apcaContrast(input, BG)),
    );
    // ...up to (at least near) the spot/incidental floor of |Lc| 25.
    expect(Math.abs(apcaContrast(out, BG))).toBeGreaterThanOrEqual(25);
  });

  it("returns a bright accent unchanged when it already clears the floor", () => {
    const input = "#b66eff"; // the magic-unicorn primary, comfortably legible
    // sanity: it really does clear the default floor on this bg
    expect(Math.abs(apcaContrast(input, BG))).toBeGreaterThanOrEqual(25);
    expect(ensureReadableAccent(input, BG)).toBe(input);
  });

  it("returns unparseable input unchanged", () => {
    expect(ensureReadableAccent("garbage", BG)).toBe("garbage");
  });
});
