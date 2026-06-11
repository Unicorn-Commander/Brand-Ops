import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  ImageIcon,
  Check,
  Download,
  History,
  Copy,
  CheckCheck,
} from "lucide-react";
import { spring } from "../../../lib/motion";
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

/**
 * One generated asset in the results grid.
 *
 * The REST API returns server-side file paths, not browser URLs, and it does not
 * serve the bytes, so the preview is a labeled placeholder rather than the image
 * (same convention as the shared AssetThumb). "Download" copies the on-disk path
 * for now; once a static-file route exists it becomes a real link.
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

  // A short "developing" reveal: when the card first mounts, a shimmer sweeps
  // over the placeholder (an image-gen riff on a print developing), then the
  // asset settles in. Skipped under reduced-motion.
  const [developing, setDeveloping] = useState(!reduce);
  useEffect(() => {
    if (reduce) return;
    const id = window.setTimeout(() => setDeveloping(false), 900);
    return () => window.clearTimeout(id);
  }, [reduce]);

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
        {/* The settled asset placeholder, scaled+faded in once developed. */}
        <motion.div
          initial={reduce ? false : { opacity: 0, scale: 1.04 }}
          animate={{ opacity: developing ? 0 : 1, scale: 1 }}
          transition={spring.card}
          className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-faint"
        >
          <ImageIcon size={22} />
          <span className="px-2 text-center text-xs leading-tight">
            {basename(file.path)}
          </span>
        </motion.div>

        {/* The "developing" reveal: a shimmer sweep over a dark plate. */}
        {developing && (
          <div
            aria-hidden
            className="absolute inset-0 animate-shimmer bg-[linear-gradient(110deg,transparent_30%,rgb(var(--accent)/0.18)_50%,transparent_70%)] bg-chrome-bg/60 bg-[length:200%_100%]"
          />
        )}

        {selected && (
          <span className="absolute right-2 top-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-accent text-chrome-bg">
            <Check size={13} />
          </span>
        )}
        <span className="absolute left-2 top-2 rounded-md bg-chrome-bg/80 px-1.5 py-0.5 text-xs tabular-nums text-text-muted">
          v{index}
        </span>
      </button>

      <div className="mt-2 flex items-center justify-between gap-1 px-0.5">
        <span className="truncate text-xs text-text-muted">
          {result.model ?? result.backend}
        </span>
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
            title={copied ? "Path copied" : "Copy file path (download not wired yet)"}
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
          >
            {copied ? <CheckCheck size={15} /> : <Copy size={15} />}
          </button>
          <a
            href={file.catalog_path ?? file.path}
            download
            onClick={(e) => {
              // Paths are server-side, not servable URLs yet. Prevent a dead
              // navigation and fall back to copying the path.
              e.preventDefault();
              void copyPath();
            }}
            title="Download (parked until a file route exists)"
            className="rounded-md p-1.5 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
          >
            <Download size={15} />
          </a>
        </div>
      </div>
    </motion.div>
  );
}
