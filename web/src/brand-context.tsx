/**
 * Active-brand context.
 *
 * Holds which brand slug is selected app-wide and applies that brand's accent
 * to the document whenever it changes. The brand switcher in the Layout sets it;
 * any screen reads it via useActiveBrand(). The brand list itself is fetched
 * here once (React Query) so the switcher and the screens share one source.
 */
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { listBrands } from "./api/client";
import { applyBrandAccent, BRAND_ACCENTS } from "./theme";
import type { BrandSummary } from "./lib/types";

const STORAGE_KEY = "brand-ops.active-brand";

interface BrandContextValue {
  /** All seeded brands (summary form). Empty until loaded. */
  brands: BrandSummary[];
  brandsLoading: boolean;
  /** The active slug, or null before any brand is chosen/loaded. */
  activeSlug: string | null;
  /** The active brand's summary, resolved from the list. */
  activeBrand: BrandSummary | null;
  setActiveSlug: (slug: string) => void;
}

const BrandContext = createContext<BrandContextValue | null>(null);

export function BrandProvider({ children }: { children: ReactNode }) {
  const { data: brands = [], isLoading: brandsLoading } = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => listBrands(signal),
  });

  const [activeSlug, setActiveSlugState] = useState<string | null>(() => {
    if (typeof localStorage === "undefined") return null;
    return localStorage.getItem(STORAGE_KEY);
  });

  const setActiveSlug = (slug: string) => {
    setActiveSlugState(slug);
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, slug);
    }
  };

  // Default the active brand to the first loaded one when nothing is selected
  // yet (or when the stored slug is no longer present).
  useEffect(() => {
    if (brands.length === 0) return;
    const exists = activeSlug && brands.some((b) => b.slug === activeSlug);
    if (!exists) {
      setActiveSlug(brands[0].slug);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brands, activeSlug]);

  const activeBrand = useMemo(
    () => brands.find((b) => b.slug === activeSlug) ?? null,
    [brands, activeSlug],
  );

  // Apply the brand accent whenever the active brand changes. A built-in suite
  // slug uses its hand-tuned BRAND_ACCENTS pairing; any other brand (a customer
  // brand) themes from its OWN colors carried on the summary, run through the
  // APCA legibility guard inside resolveAccent so a too-dark accent stays
  // readable on the dark chrome. applyBrandAccent snaps on the first application
  // (no recolor flash on load) and tweens (~280ms) on every later switch.
  // Reduced motion snaps throughout (handled inside the tween).
  useEffect(() => {
    if (!activeSlug) return;
    if (activeSlug in BRAND_ACCENTS) {
      applyBrandAccent(activeSlug);
    } else if (activeBrand?.colors) {
      applyBrandAccent(activeBrand.colors);
    } else {
      applyBrandAccent(activeSlug); // unknown slug, no colors: default pairing
    }
  }, [activeSlug, activeBrand]);

  const value = useMemo<BrandContextValue>(
    () => ({ brands, brandsLoading, activeSlug, activeBrand, setActiveSlug }),
    [brands, brandsLoading, activeSlug, activeBrand],
  );

  return <BrandContext.Provider value={value}>{children}</BrandContext.Provider>;
}

/** Read the active-brand context. Throws if used outside the provider. */
export function useActiveBrand(): BrandContextValue {
  const ctx = useContext(BrandContext);
  if (!ctx) {
    throw new Error("useActiveBrand must be used within a BrandProvider");
  }
  return ctx;
}
