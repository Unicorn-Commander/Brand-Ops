import { Search, X, Layers, LayoutGrid } from "lucide-react";
import { Button } from "../../../components/Button";
import { Select, type SelectOption } from "./Select";
import type { CatalogFilters, GroupBy } from "./catalog";
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

const GROUP_OPTIONS: { value: GroupBy; label: string }[] = [
  { value: "asset", label: "Asset type" },
  { value: "brand", label: "Brand" },
  { value: "none", label: "Newest first" },
];

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
  /** Total assets before the client-side type/backend/text filters. */
  totalCount: number;
  /** How many cards those assets collapse to, when versions are collapsed. */
  cardCount: number;
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

/** A small segmented control. Used for "group by", which is a view, not a filter. */
function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-text-faint">{label}</span>
      <div
        role="group"
        aria-label={label}
        className="inline-flex rounded-full border border-white/10 bg-chrome-bg/40 p-0.5"
      >
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={`rounded-full px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 ${
              value === o.value
                ? "bg-accent/15 text-accent"
                : "text-text-muted hover:text-text"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The Library's control surface, in three bands so a human reads it top to
 * bottom: FIND (search), NARROW (the four filters), then ARRANGE plus a plain
 * count of what is on screen. Brand and review drive the server query; asset
 * type, backend, and the search text filter the loaded set client-side.
 */
export function FilterBar({
  filters,
  onChange,
  brandOptions,
  assetOptions,
  backendOptions,
  shownCount,
  totalCount,
  cardCount,
}: FilterBarProps) {
  const hasFilter =
    filters.brand !== null ||
    filters.asset !== null ||
    filters.backend !== null ||
    filters.status !== null ||
    filters.query.trim() !== "";

  const partial = shownCount !== totalCount;
  const collapsedAway = filters.collapseVersions && cardCount < shownCount;

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/8 bg-chrome-raised/40 p-3">
      <div className="relative">
        <Search
          size={15}
          aria-hidden
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-faint"
        />
        <input
          type="search"
          value={filters.query}
          onChange={(e) => onChange({ ...filters, query: e.target.value })}
          placeholder="Search by brand, asset type, file name, or prompt"
          aria-label="Search assets"
          className="h-10 w-full rounded-xl border border-white/8 bg-chrome-bg/50 pl-9 pr-3 text-sm text-text placeholder:text-text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        />
      </div>

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

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-white/8 pt-3">
        <Segmented
          label="Group by"
          value={filters.groupBy}
          options={GROUP_OPTIONS}
          onChange={(v) => onChange({ ...filters, groupBy: v })}
        />
        <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-text-muted">
          <input
            type="checkbox"
            checked={filters.collapseVersions}
            onChange={(e) =>
              onChange({ ...filters, collapseVersions: e.target.checked })
            }
            className="h-3.5 w-3.5 accent-[rgb(var(--accent))]"
          />
          <Layers size={13} aria-hidden className="text-text-faint" />
          Stack re-runs of the same asset
        </label>

        <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-white/8 bg-chrome-bg/40 px-2.5 py-1 text-xs text-text-muted">
          <LayoutGrid size={12} aria-hidden className="text-text-faint" />
          <span className="tabular-nums text-text">{cardCount}</span>
          {collapsedAway ? (
            <>
              {cardCount === 1 ? "card" : "cards"} ·{" "}
              <span className="tabular-nums text-text">{shownCount}</span> files
            </>
          ) : partial ? (
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
                ...filters,
                brand: null,
                asset: null,
                backend: null,
                status: null,
                query: "",
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
