import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ImageIcon,
  AlertTriangle,
  Check,
  MessageSquareWarning,
  Archive,
} from "lucide-react";
import { SpotlightCard } from "../../../components";
import { staggerContainer, staggerItem, spring } from "../../../lib/motion";
import type { AssetGroup } from "./catalog";
import type { AssetRecord } from "../../../lib/types";

interface AssetGridProps {
  groups: AssetGroup[];
  /** key of the open group, for the selection ring. */
  selectedKey: string | null;
  onSelect: (group: AssetGroup) => void;
  /**
   * When viewing across brands, each card shows which brand the asset belongs
   * to. When false (the grid is filtered to one brand) the label is redundant,
   * so it is suppressed. Resolve a slug to a display name.
   */
  showBrand: boolean;
  brandName: (slug: string) => string;
}

/** Aspect ratio string from a [w, h] size; squares fall back when unknown. */
function aspectFor(size: number[]): string {
  if (size.length === 2 && size[0] > 0 && size[1] > 0) {
    return `${size[0]} / ${size[1]}`;
  }
  return "1 / 1";
}

/**
 * The preview frame for one asset. Sized to the asset aspect ratio so the
 * gallery stays stable as images load. While an image URL is loading a
 * shimmer skeleton sits over the chrome-raised block, swapping to the real
 * image on load; without a URL (the API serves only basenames) a labeled
 * placeholder shows instead of a broken image.
 */
function Preview({ asset }: { asset: AssetRecord }) {
  const src = asset.url ?? undefined;
  const [loaded, setLoaded] = useState(false);
  const [broken, setBroken] = useState(false);
  const showImage = Boolean(src) && !broken;

  return (
    <div
      className="relative w-full overflow-hidden rounded-2xl border border-white/8 bg-chrome-raised"
      style={{ aspectRatio: aspectFor(asset.size) }}
    >
      {showImage ? (
        <>
          {!loaded && (
            <div
              aria-hidden
              className="absolute inset-0 animate-shimmer bg-chrome-raised"
            />
          )}
          <img
            src={src}
            alt={`${asset.asset_type} ${asset.variant ?? ""} for ${asset.brand}`.trim()}
            loading="lazy"
            onLoad={() => setLoaded(true)}
            onError={() => setBroken(true)}
            className={`h-full w-full object-contain transition-[transform,opacity] duration-500 ease-spring group-hover:scale-[1.03] ${
              loaded ? "opacity-100" : "opacity-0"
            }`}
          />
        </>
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-faint transition-transform duration-500 ease-spring group-hover:scale-[1.03]">
          <ImageIcon size={22} />
          <span className="px-2 text-center text-xs leading-tight text-text-faint">
            {asset.file}
          </span>
        </div>
      )}
    </div>
  );
}

/**
 * A small lifecycle badge over a card, tinted on the suite palette so the review
 * state reads at a glance: amber needs_review, red changes_requested, a subtle
 * check for approved, a muted chip for archived. A record the gate never touched
 * (null status) renders nothing.
 */
function StatusBadge({ asset }: { asset: AssetRecord }) {
  const base =
    "pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium backdrop-blur";
  switch (asset.lifecycle_status) {
    case "needs_review":
      return (
        <span className={`${base} border border-amber-400/40 bg-amber-400/10 text-amber-300`}>
          <AlertTriangle size={11} aria-hidden />
          needs review
        </span>
      );
    case "changes_requested":
      return (
        <span className={`${base} border border-red-500/40 bg-red-500/10 text-red-300`}>
          <MessageSquareWarning size={11} aria-hidden />
          changes
        </span>
      );
    case "archived":
      return (
        <span
          className={`${base} bg-chrome-bg/80 text-text-faint ring-1 ring-white/10`}
          title="Archived"
        >
          <Archive size={11} aria-hidden />
          archived
        </span>
      );
    case "approved":
      return (
        <span
          className={`${base} bg-chrome-bg/80 text-text-muted ring-1 ring-white/10`}
          title="Approved"
        >
          <Check size={11} aria-hidden className="text-emerald-400" />
          approved
        </span>
      );
    default:
      return null;
  }
}

/**
 * A justified gallery of asset thumbnails, one card per logical asset (its
 * current version). A version count rides in the corner when an asset has more
 * than one render. Clicking opens the detail drawer for that asset. Cards lift
 * and light under the cursor; the image zooms slightly on hover.
 *
 * Layout is a CSS multi-column masonry so mixed aspect ratios pack tightly
 * without row gaps. Grouping is unaffected: each card is a self-contained
 * column item.
 */
export function AssetGrid({
  groups,
  selectedKey,
  onSelect,
  showBrand,
  brandName,
}: AssetGridProps) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="columns-2 gap-3 sm:columns-3 lg:columns-4 [&>*]:mb-3 [&>*]:break-inside-avoid"
    >
      {groups.map((group) => {
        const selected = group.key === selectedKey;
        const versionCount = group.versions.length;
        const meta =
          [group.current.asset_type, group.current.variant]
            .filter(Boolean)
            .join(" / ") || group.current.asset_type;
        return (
          <motion.div key={group.key} variants={staggerItem}>
            <motion.div
              whileHover={reduce ? undefined : { y: -3 }}
              transition={spring.card}
            >
              <SpotlightCard
                className={`group block w-full rounded-2xl text-left transition-[box-shadow] ${
                  selected
                    ? "ring-2 ring-accent ring-offset-2 ring-offset-chrome-bg"
                    : "hover:shadow-glow"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelect(group)}
                  className="relative block w-full rounded-2xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
                >
                  <Preview asset={group.current} />
                  <StatusBadge asset={group.current} />
                  {versionCount > 1 && (
                    <span
                      className="pointer-events-none absolute right-2 top-2 rounded-md bg-chrome-bg/80 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted ring-1 ring-white/10 backdrop-blur"
                      title={`${versionCount} versions`}
                    >
                      v{versionCount}
                    </span>
                  )}
                  <div className="space-y-0.5 px-1 pb-1 pt-2">
                    {showBrand && (
                      <p
                        className="truncate text-[11px] font-medium uppercase tracking-wide text-accent"
                        title={`Brand: ${brandName(group.current.brand)}`}
                      >
                        {brandName(group.current.brand)}
                      </p>
                    )}
                    <p
                      className="truncate text-xs font-medium text-text"
                      title={meta}
                    >
                      {meta}
                    </p>
                    <p className="truncate text-xs text-text-muted">
                      {group.current.backend ?? "backend unavailable"}
                      {group.current.size.length === 2
                        ? ` · ${group.current.size[0]}×${group.current.size[1]}`
                        : ""}
                    </p>
                  </div>
                </button>
              </SpotlightCard>
            </motion.div>
          </motion.div>
        );
      })}
    </motion.div>
  );
}
