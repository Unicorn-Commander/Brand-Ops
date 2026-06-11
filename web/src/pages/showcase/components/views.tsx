/**
 * The three Showcase renderers over one ShowcaseBrand[] model: a gallery of
 * identity cards (default), a dense list, and a folder grid. They share the
 * brand-visual primitives (logo lockup on the brand's own color, palette strip,
 * coverage ring) so the views feel like the same data seen three ways.
 */
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { ChevronRight, ImageIcon } from "lucide-react";
import { CoverageRing } from "../../../components/CoverageRing";
import { SpotlightCard } from "../../../components/SpotlightCard";
import { staggerContainer, staggerItem, spring } from "../../../lib/motion";
import { resolveAccent } from "../../../theme";
import { relativeTime } from "../../../lib/coverage";
import type { ShowcaseBrand } from "../data";

/** The logo lockup, set on the brand's own primary color. The recognition cue. */
function BrandMark({
  sb,
  className = "",
}: {
  sb: ShowcaseBrand;
  className?: string;
}) {
  const accent = resolveAccent(sb.brand.slug);
  return (
    <div
      className={`relative grid place-items-center overflow-hidden ${className}`}
      style={{
        background: `linear-gradient(150deg, ${accent.primary}, ${accent.secondary})`,
      }}
    >
      {/* A soft dark scrim so light logos keep contrast on bright palettes. */}
      <div className="absolute inset-0 bg-black/15" />
      {sb.markUrl ? (
        <img
          src={sb.markUrl}
          alt={`${sb.brand.display_name} logo`}
          className="relative max-h-[46%] max-w-[64%] object-contain drop-shadow-lg"
          loading="lazy"
        />
      ) : (
        <span className="relative font-display text-2xl font-semibold text-white/90 drop-shadow">
          {sb.brand.display_name.slice(0, 2)}
        </span>
      )}
    </div>
  );
}

/** A small "division of {parent}" badge for sub-brands. */
function ParentBadge({ parentName }: { parentName: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] uppercase tracking-wide text-text-faint">
      Part of {parentName}
    </span>
  );
}

/** Build a slug -> display-name lookup so children can name their parent. */
function nameLookup(brands: ShowcaseBrand[]): Map<string, string> {
  return new Map(brands.map((b) => [b.brand.slug, b.brand.display_name]));
}

/** A two-stop palette strip (brand primary -> secondary). */
function PaletteStrip({ slug, className = "" }: { slug: string; className?: string }) {
  const a = resolveAccent(slug);
  const stops = [a.primary, a.secondary];
  return (
    <div className={`flex h-2 overflow-hidden rounded-full ring-1 ring-white/10 ${className}`}>
      {stops.map((c, i) => (
        <span key={i} className="flex-1" style={{ background: c }} />
      ))}
    </div>
  );
}

// --- Gallery -----------------------------------------------------------------

export function BrandGallery({ brands }: { brands: ShowcaseBrand[] }) {
  const names = nameLookup(brands);
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
    >
      {brands.map((sb) => (
        <motion.div key={sb.brand.slug} variants={staggerItem} layout>
          <Link to={`/brands/${sb.brand.slug}`} className="block">
            <SpotlightCard className="glass accent-ring rounded-2xl shadow-panel transition-transform duration-300 ease-spring hover:-translate-y-1 hover:shadow-float">
              <BrandMark sb={sb} className="aspect-[16/10] w-full rounded-t-2xl" />
              <div className="absolute right-3 top-3">
                <CoverageRing pct={sb.stats.coveragePct} size={40} />
              </div>
              <div className="p-4">
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="font-display text-base font-semibold tracking-tight text-text">
                    {sb.brand.display_name}
                  </h3>
                  <span className="shrink-0 text-xs tabular-nums text-text-faint">
                    {sb.stats.total} assets
                  </span>
                </div>
                {sb.brand.tagline && (
                  <p className="mt-0.5 line-clamp-1 text-xs text-text-muted">
                    {sb.brand.tagline}
                  </p>
                )}
                {sb.brand.parent && (
                  <div className="mt-2">
                    <ParentBadge
                      parentName={names.get(sb.brand.parent) ?? sb.brand.parent}
                    />
                  </div>
                )}
                <PaletteStrip slug={sb.brand.slug} className="mt-3" />
                <p className="mt-3 text-xs text-text-faint">
                  {sb.stats.presentTypes}/{sb.stats.totalTypes} types ·{" "}
                  {relativeTime(sb.stats.lastCreated)}
                </p>
              </div>
            </SpotlightCard>
          </Link>
        </motion.div>
      ))}
    </motion.div>
  );
}

// --- List --------------------------------------------------------------------

/** One filled/hollow segment per asset type: read "which 2 are missing" at a glance. */
function SegmentedCoverage({ sb }: { sb: ShowcaseBrand }) {
  return (
    <div className="flex items-center gap-1" title={`${sb.stats.coveragePct}% coverage`}>
      {sb.stats.byType.map((t) => (
        <span
          key={t.key}
          className={`h-1.5 w-4 rounded-full ${t.present ? "bg-accent" : "bg-white/10"}`}
          title={`${t.label}: ${t.present ? t.count : "missing"}`}
        />
      ))}
    </div>
  );
}

export function BrandListView({ brands }: { brands: ShowcaseBrand[] }) {
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="glass overflow-hidden rounded-2xl shadow-panel"
    >
      {brands.map((sb, i) => (
        <motion.div key={sb.brand.slug} variants={staggerItem}>
          <Link
            to={`/brands/${sb.brand.slug}`}
            className={`group grid grid-cols-[2.5rem_1fr_auto] items-center gap-4 px-4 py-3 transition-colors hover:bg-white/[0.03] sm:grid-cols-[2.5rem_1.4fr_1fr_auto_auto] ${
              i > 0 ? "border-t border-white/8" : ""
            }`}
          >
            <BrandMark sb={sb} className="h-10 w-10 rounded-lg" />
            <div className="min-w-0">
              <p className="truncate font-medium text-text">{sb.brand.display_name}</p>
              <p className="truncate text-xs text-text-faint">
                {sb.brand.tagline ?? sb.brand.slug}
              </p>
            </div>
            <div className="hidden sm:block">
              <SegmentedCoverage sb={sb} />
            </div>
            <span className="hidden text-xs tabular-nums text-text-muted sm:block">
              {sb.stats.total} assets
            </span>
            <div className="flex items-center gap-3">
              <CoverageRing pct={sb.stats.coveragePct} size={34} />
              <ChevronRight
                size={16}
                className="text-text-faint transition-transform group-hover:translate-x-0.5"
              />
            </div>
          </Link>
        </motion.div>
      ))}
    </motion.div>
  );
}

// --- Folder ------------------------------------------------------------------

export function BrandFolderView({ brands }: { brands: ShowcaseBrand[] }) {
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4"
    >
      {brands.map((sb) => (
        <motion.div key={sb.brand.slug} variants={staggerItem} layout>
          <Link to={`/brands/${sb.brand.slug}`} className="group block">
            {/* Folder tab. */}
            <div className="ml-2 h-3 w-20 rounded-t-lg border border-b-0 border-white/8 bg-chrome-raised/70" />
            <motion.div
              whileHover={{ y: -3 }}
              transition={spring.card}
              className="glass rounded-2xl rounded-tl-none p-3 shadow-panel"
            >
              <div className="grid aspect-square grid-cols-2 grid-rows-2 gap-1.5 overflow-hidden rounded-xl">
                {Array.from({ length: 4 }).map((_, i) => {
                  const url = sb.recentUrls[i];
                  return (
                    <div
                      key={i}
                      className="grid place-items-center overflow-hidden bg-chrome-raised text-text-faint"
                    >
                      {url ? (
                        <img src={url} alt="" className="h-full w-full object-cover" loading="lazy" />
                      ) : (
                        <ImageIcon size={14} />
                      )}
                    </div>
                  );
                })}
              </div>
              <div className="mt-3 flex items-center justify-between gap-2">
                <p className="truncate text-sm font-medium text-text">
                  {sb.brand.display_name}
                </p>
                <span className="shrink-0 text-xs tabular-nums text-text-faint">
                  {sb.stats.total}
                </span>
              </div>
            </motion.div>
          </Link>
        </motion.div>
      ))}
    </motion.div>
  );
}
