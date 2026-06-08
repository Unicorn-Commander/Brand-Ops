import type { CSSProperties } from "react";

interface SpinnerProps {
  /** Pixel diameter. Defaults to 18. */
  size?: number;
  className?: string;
  /** Accessible label. Defaults to "Loading". */
  label?: string;
}

/**
 * A small indeterminate spinner that borrows the active brand accent. Used
 * inline in buttons and as a standalone loading marker.
 */
export function Spinner({ size = 18, className = "", label = "Loading" }: SpinnerProps) {
  const style: CSSProperties = {
    width: size,
    height: size,
    borderWidth: Math.max(2, Math.round(size / 9)),
  };
  return (
    <span
      role="status"
      aria-label={label}
      className={`inline-block animate-spin rounded-full border-text-faint/30 border-t-accent ${className}`}
      style={style}
    />
  );
}
