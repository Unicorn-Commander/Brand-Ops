import type { HTMLAttributes, ReactNode } from "react";

interface PanelProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  /** Optional header title rendered in a bordered top strip. */
  title?: ReactNode;
  /** Optional content rendered at the right of the header strip. */
  actions?: ReactNode;
  /** Remove inner padding on the body (e.g. for flush lists or canvases). */
  flush?: boolean;
  children?: ReactNode;
}

/**
 * A surface container in the dark chrome: a raised panel with a soft border and
 * an optional titled header. The four screens compose their layouts from these.
 */
export function Panel({
  title,
  actions,
  flush = false,
  children,
  className = "",
  ...rest
}: PanelProps) {
  return (
    <section
      className={`rounded-xl border border-chrome-border bg-chrome-panel shadow-panel ${className}`}
      {...rest}
    >
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-chrome-border px-4 py-3">
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
