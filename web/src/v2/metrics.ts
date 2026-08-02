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

/**
 * Humanize an activity target.
 *
 * The API reports a target as the catalog path `<asset_type>/<stored file>`, and
 * the stored file is a generation timestamp: `og_image/2026-08-01-22-53-57-og_image.png`.
 * Shown raw (as every activity surface did) that is 45 characters of machine
 * detail in which the only human-legible token is repeated twice. Rewrite it as
 * `<asset type, spaced> · <file without its timestamp>`, and leave anything that
 * is not a catalog path exactly as it came so a non-asset target is never mangled.
 */
export function activityTarget(target: string | null | undefined): string {
  if (!target) return "";
  const slash = target.indexOf("/");
  if (slash <= 0) return target;
  const type = target.slice(0, slash);
  const file = target.slice(slash + 1);
  // A catalog asset type is a lowercase snake_case key; anything else (a URL, a
  // nested path) is left alone.
  if (!/^[a-z][a-z0-9_]*$/.test(type)) return target;
  // Strip a leading `YYYY-MM-DD-HH-MM-SS-` generation stamp from the file name.
  const named = file.replace(/^\d{4}(?:-\d{2}){5}-/, "");
  return `${type.replace(/_/g, " ")} · ${named}`;
}

/**
 * What HAPPENED, without naming the actor.
 *
 * Surfaces that already render the actor themselves (the Portfolio ticker, the
 * Brand HQ activity list) must use this; `displayActivity` below prefixes the
 * actor and is only for the one surface that shows the line on its own. Using
 * the actor-prefixed form next to a separately rendered actor is what produced
 * "iris  iris generated · …" on every activity row in the app.
 */
export function activityDetail(event: ActivityEvent): string {
  const action = event.action || "updated";
  const target = activityTarget(event.target);
  return target ? `${action} · ${target}` : action;
}

/** `activityDetail` with the actor in front, for standalone one-line summaries. */
export function displayActivity(event: ActivityEvent): string {
  const actor = event.actor || "System";
  return `${actor} ${activityDetail(event)}`;
}

/**
 * "1 asset" / "2 assets". English pluralization for the counted nouns these
 * surfaces show; the brand covers read "1 assets" without it.
 */
export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * A brand's own foreground color at `alpha`, as an rgba() string.
 *
 * The brand covers set `--ink` from the brand record but hardcoded their
 * secondary/tertiary inks to `rgba(255,255,255,·)`. On a brand whose background
 * is light (Project Ops and Retirement Leads both ship `#fafaf9`) that painted
 * white text on a white card: the tagline, the "Part of …" chip, the asset
 * counts and the word "grade" all vanished. Deriving them from the brand's own
 * ink keeps the same visual hierarchy on dark brands and makes them legible on
 * light ones. Falls back to white for an unparseable ink so a bad brand record
 * degrades to the old behavior rather than to `rgba(NaN…)`.
 */
export function inkAlpha(ink: string, alpha: number): string {
  const hex = ink.trim().replace(/^#/, "");
  const full =
    hex.length === 3
      ? hex
          .split("")
          .map((c) => c + c)
          .join("")
      : hex;
  if (full.length !== 6 || /[^0-9a-fA-F]/.test(full)) {
    return `rgba(255,255,255,${alpha})`;
  }
  const r = parseInt(full.slice(0, 2), 16);
  const g = parseInt(full.slice(2, 4), 16);
  const b = parseInt(full.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
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

/**
 * Flatten a brand list into a depth-first tree walk: every brand appears once,
 * directly under its parent, carrying its nesting depth.
 *
 * Guarantees, because the previous flat "roots then children" pass broke all
 * three: a child never precedes its parent; a grandchild indents further than a
 * child; and NOTHING is dropped — a brand whose declared parent is not in the
 * list (filtered out, or not yet loaded) is emitted as a root rather than
 * silently disappearing. A parent cycle terminates instead of recursing forever.
 */
export function buildArchitecture<T extends { slug: string; parent?: string | null }>(
  brands: T[],
): Array<{ brand: T; depth: number }> {
  const known = new Set(brands.map((brand) => brand.slug));
  const childrenOf = new Map<string, T[]>();
  const roots: T[] = [];
  for (const brand of brands) {
    const parent = brand.parent;
    if (parent && parent !== brand.slug && known.has(parent)) {
      const bucket = childrenOf.get(parent);
      if (bucket) bucket.push(brand);
      else childrenOf.set(parent, [brand]);
    } else {
      roots.push(brand);
    }
  }
  const out: Array<{ brand: T; depth: number }> = [];
  const seen = new Set<string>();
  const walk = (brand: T, depth: number) => {
    if (seen.has(brand.slug)) return; // cycle guard
    seen.add(brand.slug);
    out.push({ brand, depth });
    for (const child of childrenOf.get(brand.slug) ?? []) walk(child, depth + 1);
  };
  for (const root of roots) walk(root, 0);
  // Anything left unvisited sat in a parent cycle; emit it rather than lose it.
  for (const brand of brands) if (!seen.has(brand.slug)) walk(brand, 0);
  return out;
}
