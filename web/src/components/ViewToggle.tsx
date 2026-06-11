import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { spring } from "../lib/motion";

export interface ViewOption<T extends string> {
  id: T;
  icon: LucideIcon;
  label: string;
}

/**
 * A segmented control where the active "pill" glides between options via a
 * shared layoutId (the Linear/Framer move), rather than a SaaS dropdown. Generic
 * over the option id type so any screen can drive its own view enum.
 */
export function ViewToggle<T extends string>({
  value,
  options,
  onChange,
  className = "",
}: {
  value: T;
  options: ViewOption<T>[];
  onChange: (v: T) => void;
  className?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label="View mode"
      className={`relative flex rounded-xl border border-white/8 bg-chrome-raised/60 p-1 ${className}`}
    >
      {options.map((o) => {
        const active = value === o.id;
        const Icon = o.icon;
        return (
          <button
            key={o.id}
            type="button"
            role="tab"
            aria-selected={active}
            title={o.label}
            onClick={() => onChange(o.id)}
            className={`relative z-10 grid h-8 w-9 place-items-center rounded-lg transition-colors ${
              active ? "text-text" : "text-text-faint hover:text-text-muted"
            }`}
          >
            {active && (
              <motion.span
                layoutId="view-toggle-pill"
                className="absolute inset-0 -z-10 rounded-lg bg-chrome-hover shadow-panel ring-1 ring-white/10"
                transition={spring.snap}
              />
            )}
            <Icon size={16} />
          </button>
        );
      })}
    </div>
  );
}
