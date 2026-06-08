import { Palette } from "lucide-react";
import { Panel } from "../../components/Panel";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { useActiveBrand } from "../../brand-context";
import { BrandList } from "./components/BrandList";
import { BrandIdentity } from "./components/BrandIdentity";
import { OnboardBrandButton } from "./components/OnboardBrandButton";

/**
 * Brands: the brand manager.
 *
 * Left, the list of brands from GET /api/brands. Selecting one loads its full
 * identity from GET /api/brands/{slug} on the right (colors, fonts, tagline,
 * voice, references) along with a read-only prompt-preview widget. Selection is
 * the app-wide active brand, so picking a brand here also themes the content
 * with that brand's accent. "Onboard a new brand" is a placeholder for the
 * autonomous BrandResearch flow, disabled until that flow ships.
 *
 * Brand data comes from the shared brand-context, which fetches the list once
 * and owns the active selection; this screen reads and sets it rather than
 * fetching the list again.
 */
export default function BrandsPage() {
  const { brands, brandsLoading, activeSlug, setActiveSlug } = useActiveBrand();

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-text">
            <Palette size={18} className="text-accent" />
            Brands
          </h1>
          <p className="mt-0.5 text-sm text-text-muted">
            Inspect a brand's identity and preview the prompts it composes.
          </p>
        </div>
        <OnboardBrandButton />
      </div>

      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Panel title="All brands" flush className="self-start">
          {brandsLoading ? (
            <div className="flex items-center justify-center gap-2 px-4 py-10 text-sm text-text-muted">
              <Spinner size={16} />
              Loading brands
            </div>
          ) : brands.length === 0 ? (
            <EmptyState
              icon={<Palette size={24} />}
              title="No brands yet"
              description="No brands were returned by the API."
            />
          ) : (
            <div className="p-2">
              <BrandList
                brands={brands}
                selectedSlug={activeSlug}
                onSelect={setActiveSlug}
              />
            </div>
          )}
        </Panel>

        <Panel flush className="min-h-[20rem]">
          {activeSlug ? (
            <BrandIdentity key={activeSlug} slug={activeSlug} />
          ) : (
            <EmptyState
              icon={<Palette size={28} />}
              title="Select a brand"
              description="Pick a brand on the left to see its colors, fonts, voice, and references."
            />
          )}
        </Panel>
      </div>
    </div>
  );
}
