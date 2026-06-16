import { describe, it, expect } from "vitest";
import { brandGrade } from "./metrics";
import type {
  AssetRecord,
  AssetType,
  BrandComplianceRollup,
  ComplianceAsset,
} from "../lib/types";

// Minimal builders: only the fields brandGrade's pipeline actually reads
// (coverageFor: asset_type + lifecycle_status; standardDimensions: rollup
// summary.total/score + per-asset status/score). The rest are filled to satisfy
// the wire types without affecting the result.
function asset(assetType: string): AssetRecord {
  return {
    brand: "acme",
    asset_type: assetType,
    variant: null,
    backend: null,
    model: null,
    mime: null,
    size: [],
    transparent: null,
    cost_cents: null,
    created_at: null,
    file: `${assetType}.png`,
    prompt: null,
    reference_images: [],
    lifecycle_status: "approved",
  };
}

function type(key: string): AssetType {
  return {
    key,
    label: key,
    width: 1,
    height: 1,
    default_backend: "x",
    transparent: false,
    notes: "",
  };
}

function complianceAsset(score: number): ComplianceAsset {
  return {
    file: "f.png",
    asset_type: "logo",
    kind: "generated",
    status: "pass",
    score,
    violations: [],
  };
}

const TYPES: AssetType[] = [type("logo"), type("app_icon"), type("og_image"), type("avatar")];

function rollup(score: number, assetScores: number[]): BrandComplianceRollup {
  return {
    brand: "acme",
    summary: {
      total: assetScores.length,
      pass: assetScores.length,
      warn: 0,
      fail: 0,
      skipped: 0,
      score,
    },
    assets: assetScores.map(complianceAsset),
  };
}

describe("brandGrade", () => {
  it("is deterministic: same inputs -> identical result every call", () => {
    const assets = [asset("logo"), asset("app_icon")];
    const r = rollup(88, [90, 86]);
    const a = brandGrade(assets, TYPES, r);
    const b = brandGrade(assets, TYPES, r);
    expect(a).toEqual(b);
    // Independent inputs that are value-equal also produce the same grade -- the
    // whole point is one-source-of-truth, not identity-sensitivity.
    const c = brandGrade(
      [asset("logo"), asset("app_icon")],
      TYPES,
      rollup(88, [90, 86]),
    );
    expect(c).toEqual(a);
  });

  it("returns the documented shape (letter/className/score/dimensions)", () => {
    const g = brandGrade([asset("logo")], TYPES, undefined);
    expect(typeof g.letter).toBe("string");
    expect(typeof g.className).toBe("string");
    expect(typeof g.score).toBe("number");
    expect(g.dimensions).toHaveLength(5);
    expect(g.dimensions.map((d) => d.key).sort()).toEqual([
      "color",
      "coverage",
      "mark",
      "type",
      "voice",
    ]);
  });

  it("grades full coverage with a strong rollup higher than empty + no rollup", () => {
    const full = brandGrade(
      TYPES.map((t) => asset(t.key)), // every required type present
      TYPES,
      rollup(95, [96, 94, 95, 95]),
    );
    const empty = brandGrade([], TYPES, undefined);
    expect(full.score).toBeGreaterThan(empty.score);
    // Full coverage (100%) drives coverage to its max; the score should land in
    // the top band.
    expect(full.dimensions.find((d) => d.key === "coverage")?.score).toBe(100);
  });

  it("treats an absent rollup as unmeasured (color/voice not measured)", () => {
    const g = brandGrade([asset("logo")], TYPES, undefined);
    const color = g.dimensions.find((d) => d.key === "color");
    const voice = g.dimensions.find((d) => d.key === "voice");
    expect(color?.measured).toBe(false);
    expect(voice?.measured).toBe(false);
  });
});
