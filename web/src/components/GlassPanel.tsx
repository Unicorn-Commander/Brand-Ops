import type { HTMLAttributes, ReactNode } from "react";

interface GlassPanelProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Optional header title rendered in a bordered top strip. */
  title?: ReactNode;
  /** Optional content rendered at the right of the header strip. */
  actions?: ReactNode;
  /** Remove inner padding on the body (e.g. for flush lists or canvases). */
  flush?: boolean;
  /** Draw the brand-accent gradient hairline border. */
  ring?: boolean;
  /** Add the accent glow halo (use sparingly, for hero/focused surfaces). */
  glow?: boolean;
  children?: ReactNode;
}

/**
 * The frosted-glass surface used across the redesigned app. It is the visual
 * sibling of Panel (same header API) but reads as translucent glass over the
 * aurora backdrop, with optional brand-accent ring and glow. Prefer this for
 * content that sits on the living background; keep solid Panel for dense,
 * data-heavy areas where blur would cost legibility.
 */
export function GlassPanel({
  title,
  actions,
  flush = false,
  ring = false,
  glow = false,
  children,
  className = "",
  ...rest
}: GlassPanelProps) {
  return (
    <section
      className={[
        "glass rounded-2xl shadow-panel",
        ring ? "accent-ring" : "",
        glow ? "shadow-glow" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...rest}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-white/8 px-4 py-3">
          {title && (
            <h2 className="text-sm font-semibold text-text">{title}</h2>
          )}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}
