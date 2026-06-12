import { useState, type CSSProperties } from "react";
import type { BrandDetail, BrandSummary } from "../lib/types";
import { brandColors } from "../v2/metrics";

type BrandLike = Pick<BrandSummary, "display_name" | "mark_url"> & Partial<BrandDetail>;

export function MarkImg({
  brand,
  className,
  style,
  imgStyle,
}: {
  brand: BrandLike;
  className?: string;
  style?: CSSProperties;
  imgStyle?: CSSProperties;
}) {
  const [failed, setFailed] = useState(false);
  const colors = brandColors(brand);
  const initial = brand.display_name.trim().charAt(0).toUpperCase() || "?";

  if (brand.mark_url && !failed) {
    return (
      <img
        className={className}
        src={brand.mark_url}
        alt=""
        aria-label={`${brand.display_name} mark`}
        onError={() => setFailed(true)}
        style={imgStyle ?? style}
      />
    );
  }

  return (
    <span
      className={className}
      aria-label={`${brand.display_name} mark unavailable`}
      role="img"
      style={{
        display: "grid",
        placeItems: "center",
        width: "100%",
        height: "100%",
        minWidth: 24,
        minHeight: 24,
        borderRadius: 12,
        background: `linear-gradient(135deg, ${colors.bg}, ${colors.primary} 52%, ${colors.accent})`,
        color: colors.ink,
        fontFamily: "var(--b-font-display)",
        fontWeight: 800,
        letterSpacing: "-0.04em",
        boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.18)",
        ...style,
      }}
    >
      {initial}
    </span>
  );
}
