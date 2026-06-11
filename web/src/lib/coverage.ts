/**
 * Brand asset-coverage model.
 *
 * "Coverage" answers: of the asset types the engine can produce, how many does
 * this brand actually have an ON-BRAND one of? It is computed against the live
 * /api/asset-types list (not a hardcoded checklist) so it always matches what
 * the engine supports, and each MISSING type is actionable — the Showcase and
 * brand sheet turn a gap into a one-click "generate this" CTA.
 *
 * Coverage is compliance-aware, not mere presence: a type counts as COVERED
 * only when the brand has at least one asset of it that has cleared review (its
 * lifecycle_status is not "needs_review" and not "archived"). So a type that
 * exists only as a flagged or archived asset still reads as a gap, and the
 * ring reflects on-brand completeness rather than raw file count.
 */
import type { AssetRecord, AssetType, BrandSummary } from "./types";

/**
 * Whether an asset counts toward coverage. Covered = has cleared review:
 * its lifecycle_status is anything except "needs_review" or "archived"
 * (so "approved", "changes_requested", and an ungated null all count). A
 * flagged or retired asset does not establish coverage for its type.
 */
export function isCovering(a: AssetRecord): boolean {
  const s = a.lifecycle_status;
  return s !== "needs_review" && s !== "archived";
}

/** Per-brand rollup used by the Showcase cards and the brand sheet. */
export interface BrandStats {
  slug: string;
  /** Total catalog assets for the brand. */
  total: number;
  /** Count of asset types that are covered (have an on-brand asset). */
  presentTypes: number;
  /** Total asset types the engine supports (denominator for coverage). */
  totalTypes: number;
  /** 0..100, rounded. Share of types covered (on-brand), not merely present. */
  coveragePct: number;
  /**
   * Per-type rollup, in the engine's asset-type order. `count` is the RAW total
   * of assets of the type (every lifecycle state). `flagged` is how many of
   * those are needs_review. `present` means COVERED: the type has at least one
   * asset that has cleared review, so it counts toward coverage.
   */
  byType: {
    key: string;
    label: string;
    count: number;
    flagged: number;
    present: boolean;
  }[];
  /** Asset types with no on-brand asset (none, or only flagged/archived). The worklist. */
  missing: { key: string; label: string }[];
  /** Total assets across the brand that are needs_review (the review backlog). */
  flagged: number;
  /** Most recent created_at across the brand's assets, or null. */
  lastCreated: string | null;
}

/** Group a flat asset list by brand slug. */
export function groupByBrand(assets: AssetRecord[]): Map<string, AssetRecord[]> {
  const map = new Map<string, AssetRecord[]>();
  for (const a of assets) {
    const list = map.get(a.brand);
    if (list) list.push(a);
    else map.set(a.brand, [a]);
  }
  return map;
}

/** Compute coverage stats for one brand from its assets and the type list. */
export function computeBrandStats(
  slug: string,
  assets: AssetRecord[],
  assetTypes: AssetType[],
): BrandStats {
  // Raw count, flagged (needs_review) count, and covered flag per type.
  const counts = new Map<string, number>();
  const flaggedByType = new Map<string, number>();
  const coveredTypes = new Set<string>();
  let flagged = 0;
  let lastCreated: string | null = null;
  for (const a of assets) {
    counts.set(a.asset_type, (counts.get(a.asset_type) ?? 0) + 1);
    if (a.lifecycle_status === "needs_review") {
      flaggedByType.set(a.asset_type, (flaggedByType.get(a.asset_type) ?? 0) + 1);
      flagged += 1;
    }
    if (isCovering(a)) coveredTypes.add(a.asset_type);
    if (a.created_at && (!lastCreated || a.created_at > lastCreated)) {
      lastCreated = a.created_at;
    }
  }

  const byType = assetTypes.map((t) => ({
    key: t.key,
    label: t.label,
    count: counts.get(t.key) ?? 0,
    flagged: flaggedByType.get(t.key) ?? 0,
    present: coveredTypes.has(t.key),
  }));

  const presentTypes = byType.filter((t) => t.present).length;
  const totalTypes = assetTypes.length || 1;

  return {
    slug,
    total: assets.length,
    presentTypes,
    totalTypes,
    coveragePct: Math.round((presentTypes / totalTypes) * 100),
    byType,
    missing: byType.filter((t) => !t.present).map((t) => ({ key: t.key, label: t.label })),
    flagged,
    lastCreated,
  };
}

/** Compute stats for every brand, keyed by slug. Brands with no assets get a
 *  zeroed rollup so the Showcase still renders a card for them. */
export function computeAllStats(
  brands: BrandSummary[],
  assets: AssetRecord[],
  assetTypes: AssetType[],
): Map<string, BrandStats> {
  const grouped = groupByBrand(assets);
  const out = new Map<string, BrandStats>();
  for (const b of brands) {
    out.set(b.slug, computeBrandStats(b.slug, grouped.get(b.slug) ?? [], assetTypes));
  }
  return out;
}

/** A coverage tone bucket, for the ring/bar color. */
export function coverageTone(pct: number): "full" | "mid" | "low" {
  if (pct >= 100) return "full";
  if (pct >= 60) return "mid";
  return "low";
}

/** Format an ISO timestamp as a short relative string ("3d ago"). Plain, no lib. */
export function relativeTime(iso: string | null): string {
  if (!iso) return "no activity yet";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const secs = Math.max(0, (Date.now() - then) / 1000);
  const mins = secs / 60;
  const hours = mins / 60;
  const days = hours / 24;
  if (days >= 1) return `${Math.round(days)}d ago`;
  if (hours >= 1) return `${Math.round(hours)}h ago`;
  if (mins >= 1) return `${Math.round(mins)}m ago`;
  return "just now";
}
