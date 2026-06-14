import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ImageIcon,
  Check,
  Download,
  History,
  Copy,
  CheckCheck,
  ShieldCheck,
  AlertTriangle,
} from "lucide-react";
import { spring, easeExpo } from "../../../lib/motion";
import type { GeneratedFile, GenerateResult } from "../../../lib/types";

interface ResultCardProps {
  result: GenerateResult;
  file: GeneratedFile;
  /** 1-based index within the batch, for the version label. */
  index: number;
  selected: boolean;
  onSelect: () => void;
  /** Re-run the same request to make another version of this asset. */
  onVersion: () => void;
}

/** Show just the filename from a server-side path for a compact label. */
function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function aspectRatio(w: number, h: number): string {
  return w > 0 && h > 0 ? `${w} / ${h}` : "1 / 1";
}

function firstHex(values: string[]): string | null {
  const joined = values.join(" ");
  return joined.match(/#[0-9a-fA-F]{6}\b/)?.[0] ?? null;
}

/**
 * One generated asset in the results grid.
 *
 * When the API returns a served `url` (a /api/assets/file link, same form the
 * catalog builds), the card renders the real generated image with the signature
 * REVEAL: it de-blurs + scales + fades in while a single soft brand-accent glow
 * pulses around the tile and settles. Reduced-motion gets a plain cross-fade.
 * When `url` is null (older results, or no URL could be formed) the card falls
 * back to the labeled placeholder icon. "Download" still copies the on-disk path
 * (the served URL is a viewer, not a forced download).
 */
export function ResultCard({
  result,
  file,
  index,
  selected,
  onSelect,
  onVersion,
}: ResultCardProps) {
  const [copied, setCopied] = useState(false);
  const reduce = useReducedMotion();
  // GenerateResult does not carry dimensions; fall back to a square frame.
  const ratio = aspectRatio(1, 1);
  const flagged = result.warnings.length > 0 || result.missing_refs.length > 0;
  const swatch = firstHex([...result.warnings, ...result.missing_refs]);
  const reason = result.warnings[0] ?? (result.missing_refs[0] ? `missing reference: ${result.missing_refs[0]}` : "gate pending");

  // The real generated image, when the API gave us a served URL. Until it
  // resolves (or when there is none) we show the placeholder.
  const hasImage = Boolean(file.url);
  const alt = `${result.brand} ${result.asset} v${index}`;

  // The signature REVEAL. `revealed` flips when the image decodes (onLoad), so
  // the de-blur lands on real pixels rather than a guessed delay. With no image
  // (placeholder fallback) or a load error we treat it as revealed immediately
  // so the tile is never stuck behind a glow. The glow is a one-shot pulse.
  const [revealed, setRevealed] = useState(!hasImage);
  const [failed, setFailed] = useState(false);
  // Belt-and-suspenders: if onLoad never fires (cached decode race), reveal
  // after a short beat so we never strand the image under blur.
  useEffect(() => {
    if (!hasImage || revealed) return;
    const id = window.setTimeout(() => setRevealed(true), 1200);
    return () => window.clearTimeout(id);
  }, [hasImage, revealed]);

  // The reveal motion recipe (spec): de-blur 16->0px + scale 0.96->1 + fade in
  // over ~400ms ease-out. Reduced motion = a plain cross-fade (no blur/scale).
  const imageInitial = reduce
    ? { opacity: 0 }
    : { opacity: 0, scale: 0.96, filter: "blur(16px)" };
  const imageAnimate = reduce
    ? { opacity: revealed ? 1 : 0 }
    : {
        opacity: revealed ? 1 : 0,
        scale: revealed ? 1 : 0.96,
        filter: revealed ? "blur(0px)" : "blur(16px)",
      };
  const imageTransition = reduce
    ? { duration: 0.3, ease: easeExpo.ease }
    : { duration: 0.4, ease: easeExpo.ease };

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(file.path);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable; no-op.
    }
  };

  return (
    <motion.div
      whileHover={reduce ? undefined : { y: -3 }}
      transition={spring.card}
      className={`group flex flex-col rounded-2xl border bg-chrome-panel/80 p-2 shadow-panel backdrop-blur transition-colors ${
        selected
          ? "border-accent shadow-glow ring-1 ring-accent/50"
          : "border-white/8"
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="relative w-full overflow-hidden rounded-xl border border-white/8 bg-chrome-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        style={{ aspectRatio: ratio }}
        title={selected ? "Selected" : "Select this version"}
      >
        {/* The real generated image, revealed once it decodes. Falls back to the
            placeholder below when there is no served URL or the load fails. */}
        {hasImage && !failed && (
          <motion.img
            src={file.url ?? undefined}
            alt={alt}
            loading="lazy"
            decoding="async"
            draggable={false}
            initial={imageInitial}
            animate={imageAnimate}
            transition={imageTransition}
            onLoad={() => setRevealed(true)}
            onError={() => {
              // The bytes did not resolve; show the labeled placeholder instead
              // of a broken-image glyph, and clear the reveal gate.
              setFailed(true);
              setRevealed(true);
            }}
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}

        {/* The one-shot brand-accent glow pulse that frames the reveal, then
            settles. Skipped under reduced motion and when no image is revealing. */}
        {hasImage && !failed && !reduce && (
          <motion.span
            aria-hidden
            initial={{ opacity: 0 }}
            animate={revealed ? { opacity: [0, 1, 0] } : { opacity: 0 }}
            transition={{ duration: 0.9, times: [0, 0.35, 1], ease: "easeOut" }}
            className="pointer-events-none absolute inset-0 rounded-xl shadow-glow"
          />
        )}

        {/* The labeled placeholder: shown until the image decodes, when there is
            no served URL, or when the load failed. */}
        {(!hasImage || failed || !revealed) && (
          <motion.div
            initial={reduce ? false : { opacity: 0, scale: 1.04 }}
            animate={{ opacity: hasImage && !failed && revealed ? 0 : 1, scale: 1 }}
            transition={spring.card}
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-text-faint"
          >
            <ImageIcon size={22} />
            <span className="px-2 text-center text-xs leading-tight">
              {basename(file.path)}
            </span>
            {/* While a real image is still developing, a soft shimmer reads as
                "incoming" rather than a stalled placeholder. */}
            {hasImage && !failed && !revealed && !reduce && (
              <span
                aria-hidden
                className="absolute inset-0 animate-shimmer bg-[linear-gradient(110deg,transparent_30%,rgb(var(--accent)/0.16)_50%,transparent_70%)] bg-[length:200%_100%]"
              />
            )}
          </motion.div>
        )}

        {selected && (
          <span className="absolute right-2 top-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-accent text-chrome-bg">
            <Check size={13} />
          </span>
        )}
        <span className="absolute left-2 top-2 rounded-md bg-chrome-bg/80 px-1.5 py-0.5 text-xs tabular-nums text-text-muted">
          v{index}
        </span>
        <span
          className={`absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.68rem] font-medium ring-1 ${
            flagged
              ? "bg-amber-500/15 text-amber-100 ring-amber-500/35"
              : "bg-accent/15 text-accent ring-accent/35"
          }`}
        >
          {flagged ? <AlertTriangle size={12} /> : <ShieldCheck size={12} />}
          {flagged ? "flagged" : "gate pending"}
        </span>
      </button>

      <div className="mt-2 flex items-start justify-between gap-2 px-0.5">
        <div className="min-w-0">
          <span className="block truncate text-xs text-text-muted">
            {result.model ?? result.backend}
          </span>
          <span className="mt-0.5 flex items-center gap-1 text-[0.68rem] text-text-faint">
            {swatch && <i className="h-3 w-3 rounded-sm ring-1 ring-white/20" style={{ background: swatch }} />}
            <span className="truncate">{reason}</span>
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            onClick={onVersion}
            title="Make another version"
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
          >
            <History size={15} />
          </button>
          <button
            type="button"
            onClick={copyPath}
            title={copied ? "Path copied" : "Copy the on-disk file path"}
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
          >
            {copied ? <CheckCheck size={15} /> : <Copy size={15} />}
          </button>
          {file.url ? (
            <a
              href={file.url}
              download={basename(file.path)}
              target="_blank"
              rel="noreferrer"
              title="Open / download the full image"
              className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
            >
              <Download size={15} />
            </a>
          ) : (
            <a
              href={file.catalog_path ?? file.path}
              download
              onClick={(e) => {
                // No served URL: the path is server-side, not navigable. Prevent
                // a dead navigation and fall back to copying the path.
                e.preventDefault();
                void copyPath();
              }}
              title="Download (no served URL for this file)"
              className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
            >
              <Download size={15} />
            </a>
          )}
        </div>
      </div>
    </motion.div>
  );
}
