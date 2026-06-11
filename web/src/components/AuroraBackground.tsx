/**
 * AuroraBackground — the app's signature living backdrop.
 *
 * Three large, blurred gradient blobs drift slowly behind everything, colored
 * from the active brand's accent CSS vars (so the whole app breathes in the
 * brand's palette and recolors when you switch brands). It is purely
 * decorative: fixed, behind content, pointer-events-none, and animated with
 * GPU-only transforms (translate3d/scale) so it holds 60fps. The OS
 * reduced-motion setting freezes the drift (see index.css).
 *
 * Rendered once at the app shell level; content sits above it on translucent
 * glass so the color reads through.
 */
export function AuroraBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-chrome-bg [contain:strict]"
    >
      {/* Three blurred accent blobs that drift on transform-only animations and
          blend with `screen` so overlaps add light (the true aurora look) over
          the near-black base. */}
      {/* Primary accent blob, top-left. */}
      <div
        className="absolute -left-[10%] -top-[15%] h-[55vmax] w-[55vmax] animate-aurora-1 rounded-full blur-[90px] will-change-transform [mix-blend-mode:screen]"
        style={{
          background:
            "radial-gradient(circle at center, rgb(var(--accent) / 0.5), transparent 62%)",
        }}
      />
      {/* Secondary accent blob, bottom-right. */}
      <div
        className="absolute -bottom-[20%] -right-[8%] h-[50vmax] w-[50vmax] animate-aurora-2 rounded-full blur-[100px] will-change-transform [mix-blend-mode:screen]"
        style={{
          background:
            "radial-gradient(circle at center, rgb(var(--accent-secondary) / 0.42), transparent 60%)",
        }}
      />
      {/* A mid blend blob to bridge the two, center-ish. */}
      <div
        className="absolute left-[35%] top-[25%] h-[40vmax] w-[40vmax] animate-aurora-3 rounded-full blur-[110px] will-change-transform [mix-blend-mode:screen]"
        style={{
          background:
            "radial-gradient(circle at center, rgb(var(--accent) / 0.3), transparent 65%)",
        }}
      />
      {/* A faint dot-grid texture and a vignette to settle the color and keep
          text legible over the brightest parts of the aurora. */}
      <div className="absolute inset-0 bg-dot-grid bg-dot-grid opacity-[0.5]" />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 120% at 50% 0%, transparent 30%, rgb(10 10 15 / 0.55) 78%, rgb(10 10 15 / 0.85) 100%)",
        }}
      />
    </div>
  );
}
