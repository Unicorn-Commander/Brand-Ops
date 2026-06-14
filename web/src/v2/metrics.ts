import type {
  ActivityEvent,
  AssetRecord,
  AssetType,
  BrandComplianceRollup,
  BrandDetail,
  BrandSummary,
} from "../lib/types";

const REQUIRED = ["logo", "app_icon", "og_image", "social_card", "podcast_cover", "avatar"];

export function gradeFromScore(scoreValue: number): {
  letter: string;
  className: string;
  score: number;
} {
  const score = Math.max(0, Math.min(100, Math.round(scoreValue)));
  if (score >= 90) return { letter: "A−", className: "g-a", score };
  if (score >= 85) return { letter: "B+", className: "g-b", score };
  if (score >= 80) return { letter: "B", className: "g-b", score };
  if (score >= 75) return { letter: "B−", className: "g-b", score };
  if (score >= 65) return { letter: "C+", className: "g-c", score };
  if (score >= 55) return { letter: "C", className: "g-c", score };
  return { letter: "D", className: "g-d", score };
}

export interface GradeDimension {
  key: "color" | "type" | "mark" | "coverage" | "voice";
  label: string;
  score: number;
  weight: number;
  note: string;
  measured: boolean;
}

export function gradeFromDimensions(dimensions: GradeDimension[]): {
  letter: string;
  className: string;
  score: number;
} {
  const totalWeight = dimensions.reduce((sum, dim) => sum + dim.weight, 0);
  const score = dimensions.reduce((sum, dim) => sum + dim.score * dim.weight, 0) / Math.max(1, totalWeight);
  return gradeFromScore(score);
}

export function standardDimensions(
  coveragePct: number,
  rollup: BrandComplianceRollup | undefined,
): GradeDimension[] {
  const scoredAssets = rollup?.assets.filter((asset) => asset.status !== "skipped") ?? [];
  const rollupScore = scoredAssets.length
    ? Math.round(scoredAssets.reduce((sum, asset) => sum + asset.score, 0) / scoredAssets.length)
    : rollup?.summary.score ?? 0;
  // When there is no compliance signal yet -- the rollup is still loading, or the
  // brand has nothing scored -- treat color + voice as UNMEASURED with the same
  // conservative default as type + mark (75), not 0. This keeps a brand's grade
  // stable while its (often slow) compliance rollup loads: it no longer flashes a
  // misleading low letter on the Portfolio and then jumps once the data arrives,
  // and it matches the honest "unmeasured -> conservative default" treatment the
  // type and mark dimensions already use. A loaded rollup uses the real score.
  const hasCompliance = Boolean(rollup?.summary.total);
  const colorScore = hasCompliance ? rollupScore : 75;
  const voiceScore = hasCompliance ? rollupScore : 75;

  return [
    {
      key: "color",
      label: "color",
      score: colorScore,
      weight: 25,
      note: hasCompliance ? "image checks from compliance rollup" : "unmeasured · conservative default",
      measured: hasCompliance,
    },
    {
      key: "type",
      label: "type",
      score: 75,
      weight: 15,
      note: "unmeasured · conservative default",
      measured: false,
    },
    {
      key: "mark",
      label: "mark",
      score: 75,
      weight: 20,
      note: "unmeasured · conservative default",
      measured: false,
    },
    {
      key: "coverage",
      label: "coverage",
      score: coveragePct,
      weight: 25,
      note: "required asset-type coverage",
      measured: true,
    },
    {
      key: "voice",
      label: "voice",
      score: voiceScore,
      weight: 15,
      note: hasCompliance ? "text checks from compliance rollup where present" : "unmeasured · conservative default",
      measured: hasCompliance,
    },
  ];
}

/**
 * The single canonical Brand Grade. Every surface (Portfolio, Brand HQ,
 * Standards) MUST call this with the SAME inputs — the brand's assets, the live
 * asset-type registry, and the compliance rollup — so one brand reads one grade
 * everywhere. Coverage uses the real asset types (never the hardcoded fallback),
 * blended with the compliance-derived dimensions. Returns the letter/score plus
 * the dimensions, so the report card can render the (honestly labeled) breakdown
 * without recomputing.
 */
export function brandGrade(
  assets: AssetRecord[],
  types: AssetType[],
  rollup: BrandComplianceRollup | undefined,
): {
  letter: string;
  className: string;
  score: number;
  dimensions: GradeDimension[];
} {
  const coverage = coverageFor(assets, types);
  const dimensions = standardDimensions(coverage.pct, rollup);
  const g = gradeFromDimensions(dimensions);
  return { ...g, dimensions };
}

export function assetsFor(assets: AssetRecord[], slug: string): AssetRecord[] {
  return assets.filter((asset) => asset.brand === slug);
}

export function coverageFor(assets: AssetRecord[], types: AssetType[]): {
  present: number;
  total: number;
  pct: number;
  missing: string[];
} {
  const required = types.length ? types.map((type) => type.key) : REQUIRED;
  const presentTypes = new Set(
    assets
      .filter((asset) => asset.lifecycle_status === "approved" || asset.lifecycle_status == null)
      .map((asset) => asset.asset_type),
  );
  const present = required.filter((key) => presentTypes.has(key)).length;
  return {
    present,
    total: required.length,
    pct: Math.round((present / Math.max(1, required.length)) * 100),
    missing: required.filter((key) => !presentTypes.has(key)),
  };
}

export function flaggedCount(assets: AssetRecord[]): number {
  return assets.filter(
    (asset) =>
      asset.lifecycle_status === "needs_review" ||
      asset.compliance?.status === "fail" ||
      asset.compliance?.status === "warn",
  ).length;
}

export function brandColors(brand: (Partial<BrandDetail> | BrandSummary) & { visual?: BrandDetail["visual"] }) {
  const visual = "visual" in brand ? brand.visual : undefined;
  const colors = visual?.colors;
  return {
    bg: colors?.background || "#07070b",
    ink: colors?.foreground || "#f2f1ee",
    primary: colors?.primary || "#9b8cff",
    accent: colors?.accent || colors?.primary || "#4cc9f0",
    muted: colors?.muted || "#26262f",
  };
}

export function displayActivity(event: ActivityEvent): string {
  const actor = event.actor || "System";
  const action = event.action || "updated";
  const target = event.target ? ` · ${event.target}` : "";
  return `${actor} ${action}${target}`;
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "recent";
  const ms = Date.now() - new Date(value).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "recent";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function complianceScore(rollup: BrandComplianceRollup | undefined): number | null {
  return rollup?.summary.score ?? null;
}
