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

/** Filters the Library can apply on top of the server-side brand/asset query. */
export interface CatalogFilters {
  brand: string | null;
  /** asset_type key, e.g. "logo". */
  asset: string | null;
  /** backend name, e.g. "openai". */
  backend: string | null;
  /** Review lifecycle; like brand, this drives the server query. */
  status: AssetLifecycleStatus | null;
}

export const EMPTY_FILTERS: CatalogFilters = {
  brand: null,
  asset: null,
  backend: null,
  status: null,
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
 */
export function groupAssets(records: AssetRecord[]): AssetGroup[] {
  const buckets = new Map<string, AssetRecord[]>();
  for (const r of records) {
    const k = identityKey(r);
    const list = buckets.get(k);
    if (list) list.push(r);
    else buckets.set(k, [r]);
  }

  const groups: AssetGroup[] = [];
  for (const [key, list] of buckets) {
    const versions = [...list].sort(byNewest);
    groups.push({ key, current: versions[0], versions });
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
 * Apply the asset-type and backend filters client-side. Brand is handled by the
 * server query (api.listAssets), so it is not re-applied here.
 */
export function applyFilters(
  records: AssetRecord[],
  filters: CatalogFilters,
): AssetRecord[] {
  return records.filter((r) => {
    if (filters.asset && r.asset_type !== filters.asset) return false;
    if (filters.backend && r.backend !== filters.backend) return false;
    return true;
  });
}
