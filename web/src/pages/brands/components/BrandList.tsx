import { BRAND_ACCENTS } from "../../../theme";
import type { BrandSummary } from "../../../lib/types";

/**
 * The left-rail list of brands. Selecting one drives the identity panel. Each
 * row shows the brand's accent swatch, its name, and (when present) its tagline
 * or parent. Purely presentational: the page owns the data and selection.
 */

interface BrandListProps {
  brands: BrandSummary[];
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
}

/** A round accent swatch for a brand, using its built-in pairing when known. */
function RowSwatch({ slug }: { slug: string }) {
  const pair = BRAND_ACCENTS[slug];
  const gradient = pair
    ? `linear-gradient(135deg, ${pair.primary}, ${pair.secondary})`
    : "linear-gradient(135deg, #b66eff, #00d4ff)";
  return (
    <span
      className="mt-0.5 h-4 w-4 shrink-0 rounded-full ring-1 ring-white/10"
      style={{ background: gradient }}
    />
  );
}

export function BrandList({ brands, selectedSlug, onSelect }: BrandListProps) {
  return (
    <ul className="flex flex-col gap-1">
      {brands.map((brand) => {
        const selected = brand.slug === selectedSlug;
        const sub = brand.tagline ?? (brand.parent ? `Part of ${brand.parent}` : null);
        return (
          <li key={brand.slug}>
            <button
              type="button"
              onClick={() => onSelect(brand.slug)}
              aria-current={selected ? "true" : undefined}
              className={`flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${
                selected
                  ? "bg-chrome-raised ring-1 ring-accent/40"
                  : "hover:bg-chrome-hover"
              }`}
            >
              <RowSwatch slug={brand.slug} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-text">
                  {brand.display_name}
                </span>
                {sub && (
                  <span className="block truncate text-xs text-text-muted">
                    {sub}
                  </span>
                )}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
