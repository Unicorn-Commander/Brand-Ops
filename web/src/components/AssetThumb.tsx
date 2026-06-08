import { useState } from "react";
import { ImageIcon } from "lucide-react";
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
  /** Called on click, e.g. to open a detail view. */
  onClick?: () => void;
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
 */
export function AssetThumb({ asset, src, onClick, className = "" }: AssetThumbProps) {
  const [broken, setBroken] = useState(false);
  const showImage = Boolean(src) && !broken;

  const meta = [asset.asset_type, asset.variant].filter(Boolean).join(" / ");

  const card = (
    <>
      <div
        className="relative w-full overflow-hidden rounded-lg border border-chrome-border bg-chrome-raised"
        style={{ aspectRatio: aspectRatio(asset.size) }}
      >
        {showImage ? (
          <img
            src={src}
            alt={`${asset.asset_type} ${asset.variant ?? ""} for ${asset.brand}`.trim()}
            loading="lazy"
            onError={() => setBroken(true)}
            className="h-full w-full object-contain"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 text-text-faint">
            <ImageIcon size={22} />
            <span className="px-2 text-center text-xs leading-tight text-text-faint">
              {asset.file}
            </span>
          </div>
        )}
      </div>
      <div className="mt-2 space-y-0.5">
        <p className="truncate text-xs font-medium text-text" title={meta}>
          {meta || asset.asset_type}
        </p>
        <p className="truncate text-xs text-text-muted">
          {asset.backend ?? "unknown backend"}
          {asset.size.length === 2 ? ` · ${asset.size[0]}×${asset.size[1]}` : ""}
        </p>
      </div>
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`group block w-full text-left transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-lg ${className}`}
      >
        {card}
      </button>
    );
  }

  return <div className={`block w-full ${className}`}>{card}</div>;
}
