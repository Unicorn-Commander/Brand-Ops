/**
 * Layout-matched loading placeholders. A shimmering block (the `.skeleton` class
 * in index.css, which self-stills under prefers-reduced-motion) shaped like the
 * real content, so a load reads ~20-30% faster than a bare spinner and the real
 * content lands into a reserved slot (no layout shift). Prefer these over Spinner
 * on data screens.
 */
import type { CSSProperties } from "react";

export function Skeleton({
  width,
  height = 16,
  radius,
  className = "",
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`skeleton ${className}`}
      aria-hidden="true"
      style={{
        width: width ?? "100%",
        height,
        ...(radius !== undefined ? { borderRadius: radius } : null),
        ...style,
      }}
    />
  );
}

/** A few skeleton lines, decreasing width, for a paragraph/label block. */
export function SkeletonText({
  lines = 3,
  className = "",
}: {
  lines?: number;
  className?: string;
}) {
  const widths = ["100%", "92%", "78%", "85%", "60%"];
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} height={12} width={widths[i % widths.length]} />
      ))}
    </div>
  );
}

/** A square/asset card placeholder with an optional caption line. */
export function SkeletonCard({
  aspect = "1 / 1",
  caption = true,
  className = "",
}: {
  aspect?: string;
  caption?: boolean;
  className?: string;
}) {
  return (
    <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
      <Skeleton style={{ aspectRatio: aspect }} height="auto" radius={12} />
      {caption && <Skeleton height={11} width="64%" />}
    </div>
  );
}
