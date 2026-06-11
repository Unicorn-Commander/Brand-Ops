/**
 * Activity — the secondary, opt-in dashboard. The cross-brand provenance feed
 * (what people and agents did) plus the coverage worklist (brands below full
 * coverage, with one-click routes into the gaps). These used to sit above the
 * Brands gallery and read as clutter; they live here now so the home stays
 * clean and visual, and this view is for when you actually want the worklist.
 */
import { Link } from "react-router-dom";
import { Activity as ActivityIcon, AlertCircle } from "lucide-react";
import { GlassPanel, ActivityFeed } from "../../components";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { CoverageRing } from "../../components/CoverageRing";
import { useShowcaseData, type ShowcaseBrand } from "../showcase/data";

/** Brands below full coverage, each missing type a chip linking into the brand. */
function NeedsAttention({ brands }: { brands: ShowcaseBrand[] }) {
  if (brands.length === 0) {
    return (
      <GlassPanel className="overflow-hidden">
        <div className="mb-3 flex items-center gap-2">
          <AlertCircle size={15} className="text-amber-400" />
          <h2 className="text-sm font-semibold text-text">Needs attention</h2>
        </div>
        <EmptyState title="All caught up" description="Every brand is at full coverage." />
      </GlassPanel>
    );
  }
  return (
    <GlassPanel className="overflow-hidden">
      <div className="mb-3 flex items-center gap-2">
        <AlertCircle size={15} className="text-amber-400" />
        <h2 className="text-sm font-semibold text-text">Needs attention</h2>
        <span className="text-xs text-text-faint">
          {brands.length} brand{brands.length === 1 ? "" : "s"} below full coverage
        </span>
      </div>
      <ul className="space-y-2">
        {brands.map((sb) => (
          <li
            key={sb.brand.slug}
            className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-white/[0.02] px-3 py-2"
          >
            <CoverageRing pct={sb.stats.coveragePct} size={30} />
            <Link
              to={`/brands/${sb.brand.slug}`}
              className="text-sm font-medium text-text hover:text-accent"
            >
              {sb.brand.display_name}
            </Link>
            <div className="flex flex-wrap gap-1.5">
              {sb.stats.missing.map((m) => (
                <Link
                  key={m.key}
                  to={`/brands/${sb.brand.slug}`}
                  className="inline-flex items-center gap-1 rounded-full border border-dashed border-amber-400/40 px-2 py-0.5 text-xs text-amber-300/90 transition-colors hover:bg-amber-400/10"
                >
                  + {m.label}
                </Link>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </GlassPanel>
  );
}

export default function ActivityPage() {
  const data = useShowcaseData();

  return (
    <div className="space-y-6">
      <div>
        <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-text-faint">
          <ActivityIcon size={13} className="text-accent" /> Activity
        </p>
        <h1 className="mt-1 font-display text-hero font-semibold tracking-tight text-text">
          What is <span className="accent-gradient">happening</span>
        </h1>
        <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
          The cross-brand activity feed and the coverage worklist, in one place.
        </p>
      </div>

      {data.isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner size={28} />
        </div>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <NeedsAttention brands={data.needsAttention} />
          <GlassPanel className="overflow-hidden">
            <div className="mb-3 flex items-center gap-2">
              <ActivityIcon size={15} className="text-accent" />
              <h2 className="text-sm font-semibold text-text">Recent activity</h2>
              <span className="text-xs text-text-faint">across all brands</span>
            </div>
            <ActivityFeed limit={20} />
          </GlassPanel>
        </div>
      )}
    </div>
  );
}
