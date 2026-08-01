import { useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ImageIcon,
  AlertTriangle,
  Check,
  MessageSquareWarning,
  Archive,
  Download,
} from "lucide-react";
import { SpotlightCard } from "../../../components";
import { Spinner } from "../../../components/Spinner";
import { staggerContainer, staggerItem, spring } from "../../../lib/motion";
import type { AssetGroup } from "./catalog";
import type { AssetRecord } from "../../../lib/types";

/**
 * Beyond this many cards we stop staggering the first-paint reveal: a huge
 * library should not animate for seconds. The first slice blur-rises in;
 * the rest appear immediately so scrolling never waits on motion.
 */
const STAGGER_CAP = 24;

/**
 * A stable id for the shared-element morph. Built from the asset's file path
 * (its catalog identity), it is matched on the drawer's hero image so a click
 * physically morphs the card thumbnail into the drawer hero. Versions share an
 * identity but each render has a distinct file, so the open version maps to the
 * card that was clicked (the group's current render).
 */
export function assetMorphId(file: string): string {
  return `asset-${file}`;
}

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
  /**
   * Multi-select (bulk review) mode. When on, a card click toggles selection
   * instead of opening the drawer, and each card wears a corner checkbox. Off
   * by default, so the normal click-to-open flow (and the drawer morph) is
   * byte-for-byte unchanged.
   */
  selecting?: boolean;
  selectedKeys?: Set<string>;
  onToggleSelect?: (group: AssetGroup) => void;
  /**
   * Save one asset's file. Rendered as a always-visible corner button on every
   * card: retrieval is the Library's primary job, so it must not hide behind a
   * hover state or a drawer. Resolves when the file has been handed to the
   * browser; rejects with a message worth showing.
   */
  onDownload?: (record: AssetRecord) => Promise<void>;
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
 *
 * The aspect ratio is clamped: an extreme banner or a very tall poster would
 * otherwise make one masonry card taller than the viewport and push everything
 * after it off the fold. Inside the clamp the image is still `object-contain`,
 * so nothing is cropped — the frame is just letterboxed.
 *
 * `morphId` ties the image to the detail drawer's hero via a shared `layoutId`:
 * every card carries its own unique id (derived from the asset file), so on open
 * framer morphs the clicked thumbnail INTO the drawer hero, and on close it
 * morphs the hero back into the same card (the card must keep its id through the
 * drawer's exit, so it is unconditional, not gated to the open card). Each id is
 * unique, so the only duplicate is the open card + its hero — exactly the morph
 * pair. Under reduced motion we drop the id so it cross-fades instead. The
 * zoom-on-hover is gated to fine pointers so a tap on touch does not leave the
 * image stuck scaled.
 */
function Preview({
  asset,
  morphId,
  children,
}: {
  asset: AssetRecord;
  morphId?: string;
  /** Corner overlays (status badge, version chip) positioned on the frame. */
  children?: ReactNode;
}) {
  const src = asset.url ?? undefined;
  const reduce = useReducedMotion();
  const [loaded, setLoaded] = useState(false);
  const [broken, setBroken] = useState(false);
  const showImage = Boolean(src) && !broken;

  return (
    <div
      className="relative w-full overflow-hidden rounded-2xl border border-white/8 bg-chrome-raised"
      style={{ aspectRatio: aspectFor(asset.size), maxHeight: "min(70vh, 460px)" }}
    >
      {showImage ? (
        <>
          {!loaded && (
            <div
              aria-hidden
              className="absolute inset-0 animate-shimmer bg-chrome-raised"
            />
          )}
          <motion.img
            // The morph source. Dropped under reduced motion so the drawer
            // cross-fades instead. Each id is unique to the asset, so it only
            // ever pairs with its own drawer hero — never another card.
            {...(morphId && !reduce ? { layoutId: morphId } : null)}
            src={src}
            alt={`${asset.asset_type} ${asset.variant ?? ""} for ${asset.brand}`.trim()}
            loading="lazy"
            onLoad={() => setLoaded(true)}
            onError={() => setBroken(true)}
            className={`h-full w-full object-contain transition-[transform,opacity] duration-500 ease-spring [@media(hover:hover)]:group-hover:scale-[1.03] ${
              loaded ? "opacity-100" : "opacity-0"
            }`}
          />
        </>
      ) : (
        <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-2 text-text-faint transition-transform duration-500 ease-spring [@media(hover:hover)]:group-hover:scale-[1.03]">
          <ImageIcon size={22} />
          <span className="w-full break-all text-center text-xs leading-tight text-text-faint">
            {asset.file}
          </span>
        </div>
      )}
      {children}
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
 * The per-card save control. A sibling of the card's open-button (never nested
 * inside it: a button inside a button is invalid, and the click would open the
 * drawer instead of saving). Always rendered, not hover-gated, so the affordance
 * exists on touch and is findable without discovering hover.
 */
function DownloadButton({
  record,
  onDownload,
}: {
  record: AssetRecord;
  onDownload: (record: AssetRecord) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const disabled = busy || !record.url;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={async (e) => {
        e.stopPropagation();
        setBusy(true);
        try {
          await onDownload(record);
        } finally {
          setBusy(false);
        }
      }}
      aria-label={`Download ${record.asset_type} for ${record.brand}`}
      title={record.url ? "Download this file" : "No file served for this asset"}
      className="absolute right-2 top-2 z-[4] grid h-8 w-8 place-items-center rounded-lg border border-white/15 bg-chrome-bg/80 text-text-muted shadow-sm backdrop-blur transition-colors hover:border-accent/60 hover:bg-chrome-bg hover:text-accent disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
    >
      {busy ? <Spinner size={14} /> : <Download size={15} />}
    </button>
  );
}

/**
 * A justified gallery of asset thumbnails, one card per asset. Clicking opens
 * the detail drawer; the corner button saves the file without opening anything.
 * A version count rides the frame when an asset has more than one render.
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
  selecting = false,
  selectedKeys,
  onToggleSelect,
  onDownload,
}: AssetGridProps) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="columns-2 gap-3 sm:columns-3 lg:columns-4 [&>*]:mb-3 [&>*]:break-inside-avoid"
    >
      {groups.map((group, i) => {
        const isSelected = selectedKeys?.has(group.key) ?? false;
        // The accent ring means "open in the drawer" normally, or "checked" in
        // select mode (where the drawer never opens).
        const ringOn = selecting ? isSelected : group.key === selectedKey;
        const versionCount = group.versions.length;
        const meta =
          [group.current.asset_type, group.current.variant]
            .filter(Boolean)
            .join(" / ") || group.current.asset_type;
        // Only the first slice blur-rises in (capped so a large library does not
        // animate for seconds); the rest mount already-shown. Reduced motion
        // skips the variant entirely and cross-fades via the static class.
        const staggered = !reduce && i < STAGGER_CAP;
        return (
          <motion.div
            key={group.key}
            variants={staggered ? staggerItem : undefined}
          >
            <motion.div
              // Hover lift + a soft brand-accent glow, fine-pointer only (a tap
              // must not leave a card stuck lifted). Press dips it slightly so a
              // click feels physical. Reduced motion drops both.
              whileHover={reduce ? undefined : { y: -3 }}
              whileTap={reduce ? undefined : { scale: 0.98 }}
              transition={spring.card}
            >
              <SpotlightCard
                className={`group block w-full rounded-2xl transition-[box-shadow] [@media(hover:none)]:!shadow-none ${
                  ringOn
                    ? "shadow-glow ring-2 ring-accent ring-offset-2 ring-offset-chrome-bg"
                    : "[@media(hover:hover)]:hover:shadow-glow"
                }`}
              >
                <button
                  type="button"
                  onClick={() =>
                    selecting ? onToggleSelect?.(group) : onSelect(group)
                  }
                  aria-label={`${selecting ? "Select" : "Open"} ${meta}${
                    showBrand ? ` for ${brandName(group.current.brand)}` : ""
                  }`}
                  aria-pressed={ringOn}
                  // Press scale is the wrapper's spring-driven whileTap (above),
                  // so no CSS active:scale here — the two would compound.
                  className="relative block w-full rounded-2xl text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-chrome-bg"
                >
                  <Preview
                    asset={group.current}
                    morphId={assetMorphId(group.current.file)}
                  >
                    <StatusBadge asset={group.current} />
                    {/* Version count rides the frame's bottom-left, clear of the
                        save button and the selection checkbox above it. */}
                    {versionCount > 1 && (
                      <span
                        className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-chrome-bg/80 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-text-muted ring-1 ring-white/10 backdrop-blur"
                        title={`${versionCount} renders of this asset`}
                      >
                        v{versionCount}
                      </span>
                    )}
                  </Preview>
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

                {/* Selection checkbox: empty ring → filled accent check. Only
                    present in select mode; otherwise the corner holds the save
                    button. Both are siblings of the card button, not children. */}
                {selecting ? (
                  <span
                    aria-hidden="true"
                    className={`pointer-events-none absolute right-2 top-2 z-[4] grid h-6 w-6 place-items-center rounded-full border-2 transition-colors duration-150 ${
                      isSelected
                        ? "border-accent bg-accent text-white"
                        : "border-white/50 bg-chrome-bg/60 text-transparent backdrop-blur [@media(hover:hover)]:group-hover:border-white/80"
                    }`}
                  >
                    {isSelected && <Check size={14} strokeWidth={3} />}
                  </span>
                ) : (
                  onDownload && (
                    <DownloadButton
                      record={group.current}
                      onDownload={onDownload}
                    />
                  )
                )}
              </SpotlightCard>
            </motion.div>
          </motion.div>
        );
      })}
    </motion.div>
  );
}
