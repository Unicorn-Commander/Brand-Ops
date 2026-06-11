/**
 * Data for the Showcase home. One hook fetches the three reads it needs
 * (brands, the full asset catalog, the asset-type list) and derives everything
 * the screen renders: per-brand coverage stats, a representative logo mark for
 * each brand's lockup, the top-line totals, and a 7-day "generations per day"
 * sparkline. All derivation is memoized so view-toggling never refetches.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { listAssets, listAssetTypes, listBrands } from "../../api/client";
import {
  computeAllStats,
  type BrandStats,
} from "../../lib/coverage";
import type { AssetRecord, AssetType, BrandSummary } from "../../lib/types";

/** A brand plus everything the Showcase needs to render its card. */
export interface ShowcaseBrand {
  brand: BrandSummary;
  stats: BrandStats;
  /** Best image to represent the brand: a logo, else an app icon, else any. */
  markUrl: string | null;
  /** A few recent asset URLs, for folder-view collages. */
  recentUrls: string[];
}

export interface ShowcaseData {
  brands: ShowcaseBrand[];
  assetTypes: AssetType[];
  totalAssets: number;
  totalBrands: number;
  /** Average coverage across brands, 0..100. */
  avgCoverage: number;
  /** Generations in the last 7 days. */
  recentCount: number;
  /** 7 buckets, oldest -> newest, counts of assets created per day. */
  spark: number[];
  /** Brands below full coverage, most incomplete first. The worklist. */
  needsAttention: ShowcaseBrand[];
  isLoading: boolean;
  isError: boolean;
}

const ASSET_PRIORITY = ["logo", "app_icon", "avatar", "social_card", "og_image"];

/** Pick the most brand-representative asset URL from a brand's assets. */
function pickMark(assets: AssetRecord[]): string | null {
  for (const type of ASSET_PRIORITY) {
    const hit = assets.find((a) => a.asset_type === type && a.url);
    if (hit?.url) return hit.url;
  }
  return assets.find((a) => a.url)?.url ?? null;
}

/** Most-recent-first URLs (created_at desc, nulls last), capped. */
function recent(assets: AssetRecord[], cap: number): string[] {
  return [...assets]
    .filter((a) => a.url)
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""))
    .slice(0, cap)
    .map((a) => a.url as string);
}

/** Build the 7-day per-day creation histogram (oldest -> newest). */
function buildSpark(assets: AssetRecord[]): { spark: number[]; recentCount: number } {
  const buckets = new Array(7).fill(0);
  const now = Date.now();
  const dayMs = 86_400_000;
  let recentCount = 0;
  for (const a of assets) {
    if (!a.created_at) continue;
    const t = new Date(a.created_at).getTime();
    if (Number.isNaN(t)) continue;
    const ageDays = Math.floor((now - t) / dayMs);
    if (ageDays >= 0 && ageDays < 7) {
      buckets[6 - ageDays] += 1;
      recentCount += 1;
    }
  }
  return { spark: buckets, recentCount };
}

export function useShowcaseData(): ShowcaseData {
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const assetsQ = useQuery({
    queryKey: ["assets", "all"],
    queryFn: ({ signal }) => listAssets({}, signal),
  });
  const typesQ = useQuery({
    queryKey: ["asset-types"],
    queryFn: ({ signal }) => listAssetTypes(signal),
  });

  return useMemo<ShowcaseData>(() => {
    const brands = brandsQ.data ?? [];
    const assets = assetsQ.data ?? [];
    const assetTypes = typesQ.data ?? [];

    const statsBySlug = computeAllStats(brands, assets, assetTypes);
    const byBrand = new Map<string, AssetRecord[]>();
    for (const a of assets) {
      const list = byBrand.get(a.brand);
      if (list) list.push(a);
      else byBrand.set(a.brand, [a]);
    }

    const showcaseBrands: ShowcaseBrand[] = brands.map((brand) => {
      const own = byBrand.get(brand.slug) ?? [];
      return {
        brand,
        stats:
          statsBySlug.get(brand.slug) ?? {
            slug: brand.slug,
            total: 0,
            presentTypes: 0,
            totalTypes: assetTypes.length || 1,
            coveragePct: 0,
            byType: [],
            missing: [],
            flagged: 0,
            lastCreated: null,
          },
        // The brand's declared canonical mark wins; fall back to guessing a
        // representative asset only when the brand has none.
        markUrl: brand.mark_url ?? pickMark(own),
        recentUrls: recent(own, 4),
      };
    });

    const totalAssets = assets.length;
    const totalBrands = brands.length;
    const avgCoverage = showcaseBrands.length
      ? Math.round(
          showcaseBrands.reduce((sum, b) => sum + b.stats.coveragePct, 0) /
            showcaseBrands.length,
        )
      : 0;
    const { spark, recentCount } = buildSpark(assets);

    const needsAttention = showcaseBrands
      .filter((b) => b.stats.coveragePct < 100)
      .sort((a, b) => a.stats.coveragePct - b.stats.coveragePct);

    return {
      brands: showcaseBrands,
      assetTypes,
      totalAssets,
      totalBrands,
      avgCoverage,
      recentCount,
      spark,
      needsAttention,
      isLoading: brandsQ.isLoading || assetsQ.isLoading || typesQ.isLoading,
      isError: brandsQ.isError || assetsQ.isError || typesQ.isError,
    };
  }, [
    brandsQ.data,
    assetsQ.data,
    typesQ.data,
    brandsQ.isLoading,
    assetsQ.isLoading,
    typesQ.isLoading,
    brandsQ.isError,
    assetsQ.isError,
    typesQ.isError,
  ]);
}
