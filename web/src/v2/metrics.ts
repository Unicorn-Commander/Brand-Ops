import type {
  ActivityEvent,
  AssetRecord,
  AssetType,
  BrandComplianceRollup,
  BrandDetail,
  BrandSummary,
} from "../lib/types";

const REQUIRED = ["logo", "app_icon", "og_image", "social_card", "podcast_cover", "avatar"];

export function gradeFromCoverage(coveragePct: number, flagged: number): {
  letter: string;
  className: string;
  score: number;
} {
  const score = Math.max(0, Math.min(100, Math.round(coveragePct - flagged * 6)));
  if (score >= 90) return { letter: "A−", className: "g-a", score };
  if (score >= 80) return { letter: "B+", className: "g-b", score };
  if (score >= 70) return { letter: "B−", className: "g-b", score };
  if (score >= 55) return { letter: "C+", className: "g-c", score };
  return { letter: "D", className: "g-d", score };
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

export function brandColors(brand: BrandDetail | BrandSummary & Partial<BrandDetail>) {
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
