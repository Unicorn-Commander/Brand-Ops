/**
 * Catalog helpers for the Library.
 *
 * The REST API (GET /api/assets) returns a flat list of AssetRecord, one per
 * generated file's sidecar. The catalog has no explicit version field, so we
 * derive "version history" here: records that share a brand + asset_type +
 * variant + backend identity are different renders of the same logical asset.
 * The newest by created_at represents that asset in the grid; the rest are its
 * prior versions. Everything below uses only AssetRecord fields.
 */
import type { AssetLifecycleStatus, AssetRecord } from "../../../lib/types";

/** How the Library sections carve up the assets in view. */
export type GroupBy = "asset" | "brand" | "none";

/** Filters the Library can apply on top of the server-side brand/asset query. */
export interface CatalogFilters {
  brand: string | null;
  /** asset_type key, e.g. "logo". */
  asset: string | null;
  /** backend name, e.g. "openai". */
  backend: string | null;
  /** Review lifecycle; like brand, this drives the server query. */
  status: AssetLifecycleStatus | null;
  /** Free-text match over brand, type, variant, file name, and prompt. */
  query: string;
  /**
   * Collapse renders that share an identity into one card with a version
   * history. OFF by default: it HIDES files (ten separately-commissioned
   * `avatar` renders for one brand share brand+type+variant+backend and so read
   * as "one asset, v10"), and the Library's first job is showing every file that
   * exists. On, it is the tidy review view.
   */
  collapseVersions: boolean;
  /** Which heading each section carries. */
  groupBy: GroupBy;
}

export const EMPTY_FILTERS: CatalogFilters = {
  brand: null,
  asset: null,
  backend: null,
  status: null,
  query: "",
  collapseVersions: false,
  groupBy: "asset",
};

/**
 * A logical asset: one current record plus the older renders that share its
 * identity. `key` is stable across reloads so it can drive selection and React
 * keys without leaning on array position.
 */
export interface AssetGroup {
  key: string;
  current: AssetRecord;
  /** All records in the group, newest first (index 0 === current). */
  versions: AssetRecord[];
}

/** The identity that makes two records versions of the same logical asset. */
export function identityKey(r: AssetRecord): string {
  const base = [r.brand, r.asset_type, r.variant ?? "", r.backend ?? ""].join("|");
  return r.kind === "imported" ? `${base}|${r.file}` : base;
}

/** Epoch millis for ordering; records without a timestamp sort last. */
function createdAtMs(r: AssetRecord): number {
  if (!r.created_at) return -Infinity;
  const t = Date.parse(r.created_at);
  return Number.isNaN(t) ? -Infinity : t;
}

/** Newest-first comparator; ties fall back to the file name for stability. */
function byNewest(a: AssetRecord, b: AssetRecord): number {
  const d = createdAtMs(b) - createdAtMs(a);
  if (d !== 0) return d;
  return b.file.localeCompare(a.file);
}

/**
 * Group records into logical assets, each ordered newest-first, and order the
 * groups themselves by their current record's recency.
 *
 * With `collapse` false (the default the Library ships) EVERY record gets its
 * own card, so nothing is hidden — but each card still carries the full version
 * list for its identity, so the drawer's history keeps working and a card opens
 * on the render you clicked. With `collapse` true only the newest render of each
 * identity gets a card and the older ones live behind it.
 */
export function groupAssets(
  records: AssetRecord[],
  collapse = false,
): AssetGroup[] {
  const buckets = new Map<string, AssetRecord[]>();
  for (const r of records) {
    const k = identityKey(r);
    const list = buckets.get(k);
    if (list) list.push(r);
    else buckets.set(k, [r]);
  }
  for (const [k, list] of buckets) buckets.set(k, [...list].sort(byNewest));

  const groups: AssetGroup[] = [];
  if (collapse) {
    for (const [key, versions] of buckets) {
      groups.push({ key, current: versions[0], versions });
    }
  } else {
    for (const r of records) {
      const versions = buckets.get(identityKey(r)) ?? [r];
      groups.push({ key: `${identityKey(r)}::${r.file}`, current: r, versions });
    }
  }
  groups.sort((a, b) => byNewest(a.current, b.current));
  return groups;
}

/** Distinct, sorted values of a record field, dropping null/empty. */
function distinct(
  records: AssetRecord[],
  pick: (r: AssetRecord) => string | null,
): string[] {
  const set = new Set<string>();
  for (const r of records) {
    const v = pick(r);
    if (v) set.add(v);
  }
  return [...set].sort((a, b) => a.localeCompare(b));
}

/**
 * The filter options actually present in a record set. Derived from the data so
 * a select never offers a value that would return nothing. Brand options come
 * from the brand list separately (the active brand may have no assets yet).
 */
export interface CatalogFacets {
  assetTypes: string[];
  backends: string[];
}

export function facetsOf(records: AssetRecord[]): CatalogFacets {
  return {
    assetTypes: distinct(records, (r) => r.asset_type),
    backends: distinct(records, (r) => r.backend),
  };
}

/**
 * Does a record match a free-text query? Matched against everything a person
 * would plausibly type to find a file again: the brand (slug and, via
 * `brandLabel`, its display name), the asset type, the variant, the stored file
 * name, the backend, and the prompt that made it.
 */
export function matchesQuery(
  r: AssetRecord,
  needle: string,
  brandLabel?: (slug: string) => string,
): boolean {
  const q = needle.trim().toLowerCase();
  if (!q) return true;
  const haystack = [
    r.brand,
    brandLabel ? brandLabel(r.brand) : "",
    r.asset_type,
    r.asset_type.replace(/_/g, " "),
    r.variant ?? "",
    r.backend ?? "",
    r.model ?? "",
    r.file,
    r.prompt ?? "",
  ]
    .join(" ")
    .toLowerCase();
  // Every whitespace-separated term must appear, so "meeting logo" narrows
  // rather than widening the way a bare substring match would.
  return q.split(/\s+/).every((term) => haystack.includes(term));
}

/**
 * Apply the asset-type, backend, and text filters client-side. Brand and review
 * status are handled by the server query (api.listAssets), so they are not
 * re-applied here.
 */
export function applyFilters(
  records: AssetRecord[],
  filters: CatalogFilters,
  brandLabel?: (slug: string) => string,
): AssetRecord[] {
  return records.filter((r) => {
    if (filters.asset && r.asset_type !== filters.asset) return false;
    if (filters.backend && r.backend !== filters.backend) return false;
    if (!matchesQuery(r, filters.query, brandLabel)) return false;
    return true;
  });
}
