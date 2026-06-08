/**
 * Small display formatters for catalog fields. Kept plain and unit-aware; none
 * of these invent data, they only render the AssetRecord fields the API gives.
 */
import type { AssetRecord } from "../../../lib/types";

/** "1024×1024", or "Unknown" when the size is not a width/height pair. */
export function formatSize(size: number[]): string {
  if (size.length === 2 && size[0] > 0 && size[1] > 0) {
    return `${size[0]}×${size[1]}`;
  }
  return "Unknown";
}

/**
 * Cost in whole cents -> a currency string. The API stores integer cents, so a
 * value of 4 reads "$0.04". null (no recorded cost) reads "Not recorded".
 */
export function formatCost(cents: number | null): string {
  if (cents === null || cents === undefined) return "Not recorded";
  if (cents === 0) return "Free";
  return `$${(cents / 100).toFixed(2)}`;
}

/**
 * An ISO timestamp -> a readable local date/time. Returns null when there is no
 * timestamp or it does not parse, so callers can omit the row.
 */
export function formatDateTime(iso: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return new Date(t).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** A short label for a record's identity, used in headings. */
export function assetTitle(r: AssetRecord): string {
  return [r.asset_type, r.variant].filter(Boolean).join(" / ") || r.asset_type;
}
