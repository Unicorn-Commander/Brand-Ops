import { motion, useReducedMotion } from "framer-motion";
import { coverageTone } from "../lib/coverage";

interface CoverageRingProps {
  /** 0..100. */
  pct: number;
  size?: number;
  stroke?: number;
  /** Hide the centered percentage label (e.g. tiny corner badges). */
  hideLabel?: boolean;
  className?: string;
}

/** Tone -> stroke color. Full = brand accent (a reward), then amber, then orange. */
function toneColor(pct: number): string {
  switch (coverageTone(pct)) {
    case "full":
      return "rgb(var(--accent))";
    case "mid":
      return "#eab308";
    default:
      return "#f97316";
  }
}

/**
 * A pure-SVG progress ring for asset-type coverage. Animates its fill on mount
 * (respecting reduced-motion) and colors by tone — a complete brand rings in the
 * brand accent. No chart library; keeps the creative-tool feel.
 */
export function CoverageRing({
  pct,
  size = 38,
  stroke = 4,
  hideLabel = false,
  className = "",
}: CoverageRingProps) {
  const reduce = useReducedMotion();
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, pct));
  const offset = c - (clamped / 100) * c;
  const color = toneColor(clamped);

  return (
    <div
      className={`relative grid place-items-center ${className}`}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${clamped}% asset-type coverage`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          className="text-white/10"
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: reduce ? offset : c }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          style={{ filter: clamped >= 100 ? "drop-shadow(0 0 4px rgb(var(--accent) / 0.6))" : undefined }}
        />
      </svg>
      {!hideLabel && (
        <span className="absolute text-[10px] font-semibold tabular-nums text-text">
          {clamped}
        </span>
      )}
    </div>
  );
}
