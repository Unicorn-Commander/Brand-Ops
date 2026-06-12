import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LibraryBig, AlertTriangle, UploadCloud } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { Button } from "../../components/Button";
import { api } from "../../api/client";
import type { AssetLifecycleStatus, AssetRecord } from "../../lib/types";
import { useActiveBrand } from "../../brand-context";
import { FilterBar } from "./components/FilterBar";
import { AssetGrid } from "./components/AssetGrid";
import { AssetDetailDrawer } from "./components/AssetDetailDrawer";
import { UploadDropzone } from "./components/UploadDropzone";
import type { SelectOption } from "./components/Select";
import {
  EMPTY_FILTERS,
  applyFilters,
  facetsOf,
  groupAssets,
  type AssetGroup,
  type CatalogFilters,
} from "./components/catalog";

/** Title-case an asset_type key like "og_image" -> "Og image" as a fallback. */
function humanizeKey(key: string): string {
  const s = key.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/**
 * sessionStorage key a caller (e.g. the command palette's "Review queue"
 * action) sets to hand the Library an initial review-status filter. The Library
 * reads it once on mount and clears it, so it lands filtered without a flash of
 * the unfiltered grid and without stickiness on later visits.
 */
const STATUS_HANDOFF_KEY = "brand-ops.library-status";

/** The lifecycle values a status handoff may carry, as a runtime guard set. */
const HANDOFF_STATUS_VALUES: ReadonlySet<string> = new Set<AssetLifecycleStatus>([
  "approved",
  "needs_review",
  "changes_requested",
  "archived",
]);

/** Read + clear the one-shot status handoff. Returns a valid status, or null. */
function readStatusHandoff(): AssetLifecycleStatus | null {
  if (typeof sessionStorage === "undefined") return null;
  const raw = sessionStorage.getItem(STATUS_HANDOFF_KEY);
  if (raw === null) return null;
  sessionStorage.removeItem(STATUS_HANDOFF_KEY);
  return HANDOFF_STATUS_VALUES.has(raw) ? (raw as AssetLifecycleStatus) : null;
}

/**
 * Library: the catalog of generated assets.
 *
 * A filter bar (brand / asset type / backend) sits over a responsive grid of
 * thumbnails read from GET /api/assets. The brand filter drives the server
 * query; asset type and backend filter the loaded set so their dropdowns offer
 * exactly what is present. Clicking a thumbnail opens a detail drawer with the
 * prompt that made it, the backend and model, size, cost, references, and the
 * version history of every render that shares the asset's identity.
 */
export default function LibraryPage() {
  const { brands, activeSlug } = useActiveBrand();
  const queryClient = useQueryClient();

  // The library has its own brand filter, seeded from the app's active brand
  // but independent after that (a user may browse one brand, or all of them).
  // The review-status filter can be pre-set by a one-shot sessionStorage
  // handoff (the "Review queue" command lands here filtered to needs_review).
  const [filters, setFilters] = useState<CatalogFilters>(() => ({
    ...EMPTY_FILTERS,
    status: readStatusHandoff(),
  }));
  const [seeded, setSeeded] = useState(false);
  useEffect(() => {
    if (!seeded && activeSlug) {
      setFilters((f) => ({ ...f, brand: activeSlug }));
      setSeeded(true);
    }
  }, [seeded, activeSlug]);

  // Drawer state: which logical asset is open, and which of its versions.
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [activeFile, setActiveFile] = useState<string | null>(null);

  // Server query, keyed on the brand and review-status filters. Asset type and
  // backend are applied client-side, so changing them does not refetch.
  const assetsKey = useMemo(
    () => ["assets", filters.brand, filters.status] as const,
    [filters.brand, filters.status],
  );
  const {
    data: records,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: assetsKey,
    queryFn: ({ signal }) =>
      api.listAssets(
        {
          brand: filters.brand ?? undefined,
          status: filters.status ?? undefined,
        },
        signal,
      ),
  });

  // A small standing count of assets needing review, scoped to the same brand
  // filter, for the header. Kept independent of the main list so it stays
  // correct even when the user has filtered the grid to Approved.
  const { data: needsReview } = useQuery({
    queryKey: ["assets", filters.brand, "needs_review"] as const,
    queryFn: ({ signal }) =>
      api.listAssets(
        { brand: filters.brand ?? undefined, status: "needs_review" },
        signal,
      ),
  });
  const needsReviewCount = needsReview?.length ?? 0;

  const all = useMemo(() => records ?? [], [records]);
  const facets = useMemo(() => facetsOf(all), [all]);
  const filtered = useMemo(
    () => applyFilters(all, filters),
    [all, filters],
  );
  const groups = useMemo(() => groupAssets(filtered), [filtered]);

  // The vault reads as editorial sections per asset type, each with its own
  // header and approved count, instead of one flat grid.
  const sections = useMemo(() => {
    const by = new Map<string, AssetGroup[]>();
    for (const g of groups) {
      const key = g.current.asset_type;
      const bucket = by.get(key);
      if (bucket) bucket.push(g);
      else by.set(key, [g]);
    }
    return [...by.entries()].map(([key, items]) => ({
      key,
      label: humanizeKey(key),
      items,
      approved: items.filter(
        (i) => (i.current.lifecycle_status ?? "approved") === "approved",
      ).length,
    }));
  }, [groups]);

  // Filter options. Brand comes from the brand list (the active brand may have
  // no assets yet); asset type and backend come from the loaded records.
  const brandOptions: SelectOption[] = useMemo(
    () => brands.map((b) => ({ value: b.slug, label: b.display_name })),
    [brands],
  );

  // Resolve a brand slug to its display name for the per-card label. Falls back
  // to the slug if the brand list has not loaded the entry yet.
  const brandName = useCallback(
    (slug: string) => brands.find((b) => b.slug === slug)?.display_name ?? slug,
    [brands],
  );
  // The Library is the cross-brand view; the brand label on each card is only
  // useful when more than one brand is in view. Filtered to one brand, it is
  // redundant, so suppress it.
  const showBrand = filters.brand === null;
  const assetOptions: SelectOption[] = useMemo(
    () =>
      facets.assetTypes.map((key) => ({ value: key, label: humanizeKey(key) })),
    [facets.assetTypes],
  );
  const backendOptions: SelectOption[] = useMemo(
    () => facets.backends.map((b) => ({ value: b, label: b })),
    [facets.backends],
  );

  // Resolve the open group from the current grouping. If a reload drops the
  // asset (e.g. brand changed), close the drawer rather than dangle.
  const openGroup: AssetGroup | null = useMemo(
    () => groups.find((g) => g.key === openKey) ?? null,
    [groups, openKey],
  );
  useEffect(() => {
    if (openKey && !openGroup) {
      setOpenKey(null);
      setActiveFile(null);
    }
  }, [openKey, openGroup]);

  function openAsset(group: AssetGroup) {
    setOpenKey(group.key);
    setActiveFile(group.current.file);
  }
  function closeDrawer() {
    setOpenKey(null);
    setActiveFile(null);
  }

  // Uploads target one brand. Use the Library's brand filter when set; otherwise
  // fall back to the app's active brand, so a drop always has a destination.
  const uploadBrand = filters.brand ?? activeSlug;
  const uploadBrandLabel = useMemo(() => {
    const found = brands.find((b) => b.slug === uploadBrand);
    return found?.display_name ?? uploadBrand ?? undefined;
  }, [brands, uploadBrand]);

  // After a successful upload, refetch so the new assets appear in the grid. The
  // server query is keyed on the brand filter; refetch reloads exactly that set.
  function handleUploaded() {
    void refetch();
  }

  // Move an asset through review (approve / request changes / archive / re-flag).
  // On success swap the returned (now restamped) record into the active list
  // cache so its badge and pill update instantly, then invalidate every assets
  // query to resync (the header count and a status-filtered grid both refetch)
  // and every activity query so the new event shows in the feeds without reload.
  const transitionMutation = useMutation({
    mutationFn: (vars: {
      record: AssetRecord;
      to: AssetLifecycleStatus;
      note?: string;
    }) =>
      api.transitionAsset({
        brand: vars.record.brand,
        file: vars.record.file,
        asset: vars.record.asset_type,
        kind: vars.record.kind ?? "generated",
        to: vars.to,
        note: vars.note,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<AssetRecord[]>(assetsKey, (prev) =>
        prev
          ? prev.map((r) => (r.file === updated.file ? updated : r))
          : prev,
      );
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      void queryClient.invalidateQueries({ queryKey: ["activity"] });
    },
  });

  const transitioningFile =
    transitionMutation.isPending && transitionMutation.variables
      ? transitionMutation.variables.record.file
      : null;

  const handleTransition = useCallback(
    (record: AssetRecord, to: AssetLifecycleStatus, note?: string) => {
      transitionMutation.mutate({ record, to, note });
    },
    [transitionMutation],
  );

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end gap-5 pt-3">
        <div>
          <span className="microlabel">Library · every brand</span>
          <h1
            className="mt-1 font-bold"
            style={{
              fontFamily: "var(--c-font-display)",
              fontSize: 38,
              letterSpacing: "-0.025em",
            }}
          >
            The vault
          </h1>
          <p className="annotation mt-2">
            Each asset lives with its brand; this is the lens across all of
            them. Neutral mats — judge the work, not the wall.
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3 pb-1">
          {needsReviewCount > 0 && (
            <button
              type="button"
              onClick={() =>
                setFilters((f) => ({ ...f, status: "needs_review" }))
              }
              className="inline-flex items-center gap-1.5 rounded-full border border-amber-400/40 bg-amber-400/10 px-2.5 py-1 text-xs font-medium text-amber-300 transition-colors hover:bg-amber-400/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/50"
              title="Show assets needing review"
            >
              <span className="tabular-nums">{needsReviewCount}</span> need
              review
            </button>
          )}
          {isFetching && !isLoading && (
            <span className="flex items-center gap-2 text-xs text-text-muted">
              <Spinner size={14} /> Refreshing
            </span>
          )}
        </div>
      </header>

      <details className="rounded-2xl border border-white/8 bg-white/[0.02] open:bg-white/[0.03]">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-3 text-sm text-text-muted transition-colors hover:text-text [&::-webkit-details-marker]:hidden">
          <UploadCloud size={15} />
          <span>
            Add assets — drop images into{" "}
            <b className="font-medium text-text">
              {uploadBrandLabel ?? "the active brand"}
            </b>
            , staged through the gate
          </span>
          <span className="ml-auto text-xs text-text-faint">expand ▾</span>
        </summary>
        <div className="px-5 pb-5">
          <UploadDropzone
            brand={uploadBrand}
            brandLabel={uploadBrandLabel}
            onUploaded={handleUploaded}
          />
        </div>
      </details>

      <div className="space-y-5">
        <FilterBar
            filters={filters}
            onChange={setFilters}
            brandOptions={brandOptions}
            assetOptions={assetOptions}
            backendOptions={backendOptions}
            shownCount={filtered.length}
            totalCount={all.length}
          />

          {isLoading ? (
            <div className="flex items-center justify-center gap-3 py-16 text-sm text-text-muted">
              <Spinner size={18} /> Loading assets
            </div>
          ) : isError ? (
            <EmptyState
              icon={<AlertTriangle size={26} />}
              title="Could not load the catalog"
              description={
                error instanceof Error
                  ? error.message
                  : "The asset catalog request failed."
              }
              action={
                <Button variant="secondary" onClick={() => refetch()}>
                  Try again
                </Button>
              }
            />
          ) : groups.length === 0 ? (
            <EmptyState
              icon={<LibraryBig size={28} />}
              title={
                all.length === 0
                  ? "No assets yet"
                  : "No assets match these filters"
              }
              description={
                all.length === 0
                  ? "Generated assets appear here once a generation run completes. Head to Studio to create the first one."
                  : "Adjust the asset type or backend filters to see more."
              }
              action={
                all.length > 0 ? (
                  <Button
                    variant="secondary"
                    onClick={() =>
                      setFilters((f) => ({
                        ...f,
                        asset: null,
                        backend: null,
                      }))
                    }
                  >
                    Clear type and backend
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="space-y-8">
              {sections.map((section) => (
                <section key={section.key}>
                  <div className="mb-3 flex items-baseline gap-3">
                    <h2
                      className="text-lg font-semibold tracking-tight"
                      style={{ fontFamily: "var(--c-font-display)" }}
                    >
                      {section.label}
                    </h2>
                    <span className="font-mono text-[11px] text-text-faint">
                      {section.items.length} · {section.approved} approved
                    </span>
                    <span className="h-px flex-1 bg-white/8" />
                  </div>
                  <AssetGrid
                    groups={section.items}
                    selectedKey={openKey}
                    onSelect={openAsset}
                    showBrand={showBrand}
                    brandName={brandName}
                  />
                </section>
              ))}
            </div>
          )}
        </div>

      <AssetDetailDrawer
        group={openGroup}
        activeFile={activeFile}
        onSelectVersion={setActiveFile}
        onClose={closeDrawer}
        onTransition={handleTransition}
        transitioning={
          activeFile !== null && transitioningFile === activeFile
        }
      />
    </div>
  );
}
