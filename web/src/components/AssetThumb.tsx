import { useState } from "react";
import { Check, ImageIcon, Maximize2 } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { spring } from "../lib/motion";
import { useLightbox } from "./ImageLightbox";
import type { AssetRecord } from "../lib/types";

interface AssetThumbProps {
  asset: AssetRecord;
  /**
   * Optional resolved image URL. The REST API returns only the file basename
   * (`asset.file`); it does not serve the bytes. When a static-file route exists
   * a parent can pass the URL here and the real image renders. Without it, we
   * show a labeled placeholder rather than a broken image.
   */
  src?: string;
  /** Called on click, e.g. to open a detail view (or toggle selection). */
  onClick?: () => void;
  /**
   * Whether this card reads as selected: a brand-accent ring plus a checkmark
   * that springs in. Backwards-compatible — omit it and the card is never
   * selected.
   */
  selected?: boolean;
  /**
   * When true the card renders a persistent corner badge (an empty circle that
   * fills when selected), the affordance for multi-select grids. When false
   * (the default) only a selected card shows its check, so single-open grids
   * stay clean.
   */
  selectable?: boolean;
  className?: string;
}

function aspectRatio(size: number[]): string {
  if (size.length === 2 && size[0] > 0 && size[1] > 0) {
    return `${size[0]} / ${size[1]}`;
  }
  return "1 / 1";
}

/**
 * Renders one catalog asset record as a thumbnail card: the image when a URL is
 * available, otherwise a placeholder, with the asset type, variant, and backend
 * beneath. Small and composable; the Library grid lays these out.
 *
 * Craft: on a real pointer (`@media (hover:hover)`, so it never sticks on touch)
 * the card lifts and a soft brand-accent spotlight blooms behind it in under
 * 150ms; pressing it dips to 0.98 for a tactile beat. A selected card carries a
 * brand-accent ring and a checkmark that springs in. Font weights are constant
 * across every state so nothing reflows, and all of it respects
 * prefers-reduced-motion.
 */
export function AssetThumb({
  asset,
  src,
  onClick,
  selected = false,
  selectable = false,
  className = "",
}: AssetThumbProps) {
  const [broken, setBroken] = useState(false);
  const reduce = useReducedMotion();
  const { open } = useLightbox();
  const showImage = Boolean(src) && !broken;

  const meta = [asset.asset_type, asset.variant].filter(Boolean).join(" / ");
  const dims = asset.size.length === 2 ? `${asset.size[0]}×${asset.size[1]}` : null;
  const showBadge = selectable || selected;
  const imgAlt = `${asset.asset_type} ${asset.variant ?? ""} for ${asset.brand}`
    .replace(/\s+/g, " ")
    .trim();

  const card = (
    <>
      <div
        className={`relative w-full overflow-hidden rounded-lg border bg-chrome-raised transition-[border-color,box-shadow] duration-150 ${
          selected
            ? "border-accent/70 shadow-[0_0_0_1px_rgb(var(--accent)/0.7),0_0_28px_-8px_rgb(var(--accent)/0.5)]"
            : "border-chrome-border"
        }`}
        style={{ aspectRatio: aspectRatio(asset.size) }}
      >
        {/* Brand-accent spotlight: blooms only on a true hover pointer, and only
            when the card is not already wearing its selected glow. */}
        {!selected && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-[1] rounded-lg opacity-0 transition-opacity duration-150 [@media(hover:hover)]:group-hover:opacity-100"
            style={{
              background:
                "radial-gradient(120% 90% at 50% -10%, rgb(var(--accent) / 0.18), transparent 60%)",
            }}
          />
        )}

        {showImage ? (
          <>
            <img
              src={src}
              alt={imgAlt}
              loading="lazy"
              decoding="async"
              onError={() => setBroken(true)}
              className="relative z-0 h-full w-full object-contain"
            />
            {/* Enlarge affordance. stopPropagation so a wrapping card's onClick
                (selection / open) never fires; it only opens the lightbox. */}
            <button
              type="button"
              aria-label="Enlarge"
              onClick={(e) => {
                e.stopPropagation();
                if (src) open(src, imgAlt);
              }}
              className="absolute bottom-1.5 right-1.5 z-[2] grid h-6 w-6 place-items-center rounded-md bg-black/40 text-white opacity-0 backdrop-blur transition-opacity duration-150 hover:bg-black/60 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 [@media(hover:hover)]:group-hover:opacity-100"
            >
              <Maximize2 size={12} />
            </button>
          </>
        ) : (
          <div className="relative z-0 flex h-full w-full select-none flex-col items-center justify-center gap-2 text-text-faint">
            <ImageIcon size={22} aria-hidden="true" />
            <span className="px-2 text-center text-xs leading-tight text-text-faint">
              {asset.file}
            </span>
          </div>
        )}

        {/* Selection badge. In selectable grids it is always present (empty ring
            → filled check); otherwise it appears only once selected. */}
        {showBadge && (
          <span
            aria-hidden="true"
            className={`absolute right-1.5 top-1.5 z-[2] grid h-5 w-5 place-items-center rounded-full border transition-colors duration-150 ${
              selected
                ? "border-accent bg-accent text-white"
                : "border-white/40 bg-black/30 text-transparent backdrop-blur-sm [@media(hover:hover)]:group-hover:border-white/70"
            }`}
          >
            <AnimatePresence initial={false}>
              {selected && (
                <motion.span
                  key="check"
                  initial={reduce ? false : { scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  exit={reduce ? { opacity: 0 } : { scale: 0, opacity: 0 }}
                  transition={spring.snap}
                  className="grid place-items-center"
                >
                  <Check size={12} strokeWidth={3} />
                </motion.span>
              )}
            </AnimatePresence>
          </span>
        )}
      </div>
      <div className="mt-2 select-none space-y-0.5">
        <p className="truncate text-xs font-medium text-text" title={meta}>
          {meta || asset.asset_type}
        </p>
        <p className="truncate text-xs text-text-muted">
          {asset.backend ?? "backend unavailable"}
          {dims ? (
            <>
              {" · "}
              <span className="tabular">{dims}</span>
            </>
          ) : null}
        </p>
      </div>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-pressed={selectable ? selected : undefined}
        className={`group block w-full select-none rounded-lg text-left transition-transform duration-150 will-change-transform hover:-translate-y-0.5 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${className}`}
      >
        {card}
      </button>
    );
  }

  return <div className={`block w-full ${className}`}>{card}</div>;
}
