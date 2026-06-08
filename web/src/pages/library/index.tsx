import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LibraryBig, AlertTriangle } from "lucide-react";
import { Panel } from "../../components/Panel";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { Button } from "../../components/Button";
import { api } from "../../api/client";
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

  // The library has its own brand filter, seeded from the app's active brand
  // but independent after that (a user may browse one brand, or all of them).
  const [filters, setFilters] = useState<CatalogFilters>(EMPTY_FILTERS);
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

  // Server query, keyed on the brand filter. Asset type and backend are applied
  // client-side, so changing them does not refetch.
  const {
    data: records,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ["assets", filters.brand],
    queryFn: ({ signal }) =>
      api.listAssets({ brand: filters.brand ?? undefined }, signal),
  });

  const all = useMemo(() => records ?? [], [records]);
  const facets = useMemo(() => facetsOf(all), [all]);
  const filtered = useMemo(
    () => applyFilters(all, filters),
    [all, filters],
  );
  const groups = useMemo(() => groupAssets(filtered), [filtered]);

  // Filter options. Brand comes from the brand list (the active brand may have
  // no assets yet); asset type and backend come from the loaded records.
  const brandOptions: SelectOption[] = useMemo(
    () => brands.map((b) => ({ value: b.slug, label: b.display_name })),
    [brands],
  );
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

  return (
    <div className="space-y-4">
      <Panel title="Add assets">
        <UploadDropzone
          brand={uploadBrand}
          brandLabel={uploadBrandLabel}
          onUploaded={handleUploaded}
        />
      </Panel>

      <Panel
        title="Library"
        actions={
          isFetching && !isLoading ? (
            <span className="flex items-center gap-2 text-xs text-text-muted">
              <Spinner size={14} /> Refreshing
            </span>
          ) : undefined
        }
      >
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
            <AssetGrid
              groups={groups}
              selectedKey={openKey}
              onSelect={openAsset}
            />
          )}
        </div>
      </Panel>

      <AssetDetailDrawer
        group={openGroup}
        activeFile={activeFile}
        onSelectVersion={setActiveFile}
        onClose={closeDrawer}
      />
    </div>
  );
}
