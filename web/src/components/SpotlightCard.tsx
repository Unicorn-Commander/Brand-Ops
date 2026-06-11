import { useRef, type HTMLAttributes, type ReactNode } from "react";

interface SpotlightCardProps extends HTMLAttributes<HTMLDivElement> {
  /** Override the spotlight color. Defaults to the brand accent. */
  spotlight?: string;
  children?: ReactNode;
}

/**
 * A card that tracks the cursor with a soft radial accent glow — the premium
 * "the surface is lit by where you point" effect. The glow position is written
 * to two CSS vars on pointer move (no React re-render per frame), so it stays
 * cheap. Compose any content inside; pair with `glass` / `accent-ring` classes
 * via className. Falls back to a static, calm surface where pointer events are
 * absent (touch / reduced input).
 */
export function SpotlightCard({
  spotlight = "rgb(var(--accent) / 0.18)",
  children,
  className = "",
  style,
  ...rest
}: SpotlightCardProps) {
  const ref = useRef<HTMLDivElement>(null);

  function onMove(e: React.PointerEvent<HTMLDivElement>) {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - rect.left}px`);
    el.style.setProperty("--my", `${e.clientY - rect.top}px`);
  }

  return (
    <div
      ref={ref}
      onPointerMove={onMove}
      className={["group/spot relative overflow-hidden", className]
        .filter(Boolean)
        .join(" ")}
      style={style}
      {...rest}
    >
      {/* The spotlight layer. Hidden until hover, follows --mx/--my. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover/spot:opacity-100"
        style={{
          background: `radial-gradient(420px circle at var(--mx, 50%) var(--my, 50%), ${spotlight}, transparent 60%)`,
        }}
      />
      {children}
    </div>
  );
}
