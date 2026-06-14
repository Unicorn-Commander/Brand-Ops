import { type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { easeExpo, easeOutFast } from "../lib/motion";

interface OverrideFieldProps {
  /** Field label (e.g. "Quality" in Simple, "Model / backend" in Advanced). */
  label: string;
  /**
   * The value the system would pick on its own, shown read-only beside
   * "Auto (recommended)" until the user overrides. Pass a friendly name; pass
   * the raw identifier via `identifier` to show it in technical contexts.
   */
  resolvedValue: ReactNode;
  /** Whether the override control is revealed. */
  overridden: boolean;
  onOverriddenChange: (next: boolean) => void;
  /**
   * The real control to reveal when overridden (e.g. a <select>). It owns its
   * own value; this component only governs whether it is shown.
   */
  children: ReactNode;
  /**
   * Optional raw technical identifier (backend/model id) shown as a monospace
   * tail on the resolved line. Use in Advanced mode so pros see exactly what
   * "Auto" resolves to without having to override.
   */
  identifier?: string | null;
  /** Disable the whole field (e.g. while generating). */
  disabled?: boolean;
}

/**
 * A control demoted behind an explicit opt-in, the Vercel "Override" pattern.
 *
 * The default is the recommendation: we show "Auto (recommended) — <value>"
 * read-only, so a newcomer never has to understand the underlying knob to get a
 * good result. An "Override" toggle (a real button with aria-pressed) reveals
 * the actual control only for the operator who wants to drive it. This keeps the
 * dense choice reachable in every mode while letting it stay out of the way:
 * nothing is hidden, it is one click behind information scent.
 */
export function OverrideField({
  label,
  resolvedValue,
  overridden,
  onOverriddenChange,
  children,
  identifier,
  disabled = false,
}: OverrideFieldProps) {
  const reduce = useReducedMotion();

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-text-muted">{label}</span>
        <button
          type="button"
          aria-pressed={overridden}
          disabled={disabled}
          onClick={() => onOverriddenChange(!overridden)}
          className={
            "rounded-md px-1.5 py-0.5 text-[11px] font-medium transition-colors " +
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 " +
            "disabled:cursor-not-allowed disabled:opacity-50 " +
            (overridden
              ? "bg-accent/15 text-accent ring-1 ring-accent/30"
              : "text-text-faint hover:text-text-muted hover:bg-chrome-hover")
          }
        >
          {overridden ? "Use Auto" : "Override"}
        </button>
      </div>

      <AnimatePresence initial={false}>
        {overridden ? (
          <motion.div
            key="control"
            initial={reduce ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={easeExpo}
            className="overflow-hidden"
          >
            {children}
          </motion.div>
        ) : (
          <motion.div
            key="auto"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={easeOutFast}
            className={
              "flex h-9 min-w-0 items-center gap-1.5 rounded-lg border border-white/8 " +
              "bg-chrome-raised/40 px-2.5 text-sm text-text-muted"
            }
          >
            <span className="shrink-0 text-text-faint">Auto (recommended)</span>
            <span aria-hidden className="shrink-0 text-text-faint">
              —
            </span>
            <span className="truncate text-text">{resolvedValue}</span>
            {identifier && (
              <span className="ml-auto shrink-0 truncate font-mono text-[11px] text-text-faint">
                {identifier}
              </span>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
