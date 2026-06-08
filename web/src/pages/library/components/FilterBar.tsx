import { X } from "lucide-react";
import { Button } from "../../../components/Button";
import { Select, type SelectOption } from "./Select";
import type { CatalogFilters } from "./catalog";

interface FilterBarProps {
  filters: CatalogFilters;
  onChange: (next: CatalogFilters) => void;
  /** Brand options (slug -> label). From the brand list, not just assets. */
  brandOptions: SelectOption[];
  /** Asset-type options present in the loaded catalog. */
  assetOptions: SelectOption[];
  /** Backend options present in the loaded catalog. */
  backendOptions: SelectOption[];
  /** Count of assets after filtering, for the summary line. */
  shownCount: number;
  /** Total assets before the client-side type/backend filters. */
  totalCount: number;
}

/** null <-> "" sentinel so the Select keeps using empty-string for "all". */
function toValue(v: string | null): string {
  return v ?? "";
}
function fromValue(v: string): string | null {
  return v === "" ? null : v;
}

/**
 * The Library filter bar: brand, asset type, and backend selects plus a clear
 * control and a short count of what is shown. Brand drives the server query;
 * asset type and backend filter the loaded set client-side.
 */
export function FilterBar({
  filters,
  onChange,
  brandOptions,
  assetOptions,
  backendOptions,
  shownCount,
  totalCount,
}: FilterBarProps) {
  const hasFilter =
    filters.brand !== null ||
    filters.asset !== null ||
    filters.backend !== null;

  const countLabel =
    shownCount === totalCount
      ? `${totalCount} ${totalCount === 1 ? "asset" : "assets"}`
      : `${shownCount} of ${totalCount} shown`;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Select
          label="Brand"
          allLabel="All brands"
          value={toValue(filters.brand)}
          options={brandOptions}
          onChange={(v) => onChange({ ...filters, brand: fromValue(v) })}
        />
        <Select
          label="Asset type"
          allLabel="All types"
          value={toValue(filters.asset)}
          options={assetOptions}
          onChange={(v) => onChange({ ...filters, asset: fromValue(v) })}
        />
        <Select
          label="Backend"
          allLabel="All backends"
          value={toValue(filters.backend)}
          options={backendOptions}
          onChange={(v) => onChange({ ...filters, backend: fromValue(v) })}
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-text-muted">{countLabel}</p>
        {hasFilter && (
          <Button
            size="sm"
            variant="ghost"
            icon={<X size={14} />}
            onClick={() =>
              onChange({ brand: null, asset: null, backend: null })
            }
          >
            Clear filters
          </Button>
        )}
      </div>
    </div>
  );
}
