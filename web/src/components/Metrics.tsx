import { useEffect } from "react";
import {
  animate,
  motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "framer-motion";

/**
 * A number that counts up to its value on mount — a subtle "alive" cue for the
 * dashboard metrics. Uses a MotionValue (no per-frame React re-render) and
 * tabular figures so the digits don't jitter. Reduced-motion shows the final
 * value immediately.
 */
export function AnimatedNumber({
  value,
  className = "",
}: {
  value: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const mv = useMotionValue(reduce ? value : 0);
  const text = useTransform(mv, (v) => Math.round(v).toLocaleString());

  useEffect(() => {
    if (reduce) {
      mv.set(value);
      return;
    }
    const controls = animate(mv, value, { duration: 0.9, ease: "easeOut" });
    return controls.stop;
  }, [value, mv, reduce]);

  return <motion.span className={`tabular-nums ${className}`}>{text}</motion.span>;
}

/** A tiny SVG sparkline — momentum, not a chart. Inherits color via currentColor. */
export function Sparkline({
  data,
  width = 64,
  height = 22,
  className = "",
}: {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1);
  const pts = data
    .map((d, i) => {
      const x = (i / (data.length - 1)) * width;
      const y = height - (d / max) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={className}
      fill="none"
      aria-hidden
    >
      <polyline
        points={pts}
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export interface Stat {
  label: string;
  value: number;
  /** Optional sparkline series (e.g. generations per day). */
  spark?: number[];
  /** Optional suffix rendered small after the number (e.g. "%"). */
  suffix?: string;
}

/**
 * The editorial metric strip: big tabular numbers, quiet uppercase labels,
 * hairline dividers, no boxed KPI cards. Sits on glass over the aurora.
 */
export function StatRow({ stats, className = "" }: { stats: Stat[]; className?: string }) {
  return (
    <div
      className={`grid grid-cols-2 divide-x divide-white/8 sm:grid-cols-4 ${className}`}
    >
      {stats.map((s) => (
        <div key={s.label} className="px-5 py-4">
          <div className="text-[11px] uppercase tracking-wider text-text-faint">
            {s.label}
          </div>
          <div className="mt-1 flex items-end gap-2">
            <span className="font-display text-3xl font-semibold tracking-tight text-text">
              <AnimatedNumber value={s.value} />
              {s.suffix && <span className="text-lg text-text-muted">{s.suffix}</span>}
            </span>
            {s.spark && <Sparkline data={s.spark} className="mb-1.5 text-accent" />}
          </div>
        </div>
      ))}
    </div>
  );
}
