import { X } from "lucide-react";
import { Button } from "../../../components/Button";
import { Select, type SelectOption } from "./Select";
import type { CatalogFilters } from "./catalog";
import type { AssetLifecycleStatus } from "../../../lib/types";

/** The lifecycle choices the status filter offers beyond "all". Ordered as the
 *  review flow runs: incoming work first, then cleared, then the side states. */
const STATUS_OPTIONS: SelectOption[] = [
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "changes_requested", label: "Changes requested" },
  { value: "archived", label: "Archived" },
];

/** The lifecycle values the status filter accepts, as a runtime guard set. */
const STATUS_VALUES: ReadonlySet<string> = new Set(
  STATUS_OPTIONS.map((o) => o.value),
);

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

/** Like fromValue, but narrowed to the lifecycle union the status filter uses. */
function fromStatusValue(v: string): AssetLifecycleStatus | null {
  return STATUS_VALUES.has(v) ? (v as AssetLifecycleStatus) : null;
}

/**
 * The Library filter bar: brand, asset type, and backend selects plus a clear
 * control and a short count of what is shown. Brand drives the server query;
 * asset type and backend filter the loaded set client-side. Sits in a glass
 * strip so it reads as a control surface over the gallery.
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
    filters.backend !== null ||
    filters.status !== null;

  const partial = shownCount !== totalCount;

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/8 bg-chrome-raised/40 p-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
        <Select
          label="Review"
          allLabel="All"
          value={toValue(filters.status)}
          options={STATUS_OPTIONS}
          onChange={(v) =>
            onChange({ ...filters, status: fromStatusValue(v) })
          }
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-white/8 bg-chrome-bg/40 px-2.5 py-1 text-xs text-text-muted">
          <span className="tabular-nums text-text">{shownCount}</span>
          {partial ? (
            <>
              of <span className="tabular-nums text-text">{totalCount}</span> shown
            </>
          ) : (
            <>{totalCount === 1 ? "asset" : "assets"}</>
          )}
        </span>
        {hasFilter && (
          <Button
            size="sm"
            variant="ghost"
            icon={<X size={14} />}
            onClick={() =>
              onChange({
                brand: null,
                asset: null,
                backend: null,
                status: null,
              })
            }
          >
            Clear filters
          </Button>
        )}
      </div>
    </div>
  );
}
