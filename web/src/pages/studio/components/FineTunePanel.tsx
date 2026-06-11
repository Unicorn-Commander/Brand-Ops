import { type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { SlidersHorizontal, ChevronDown } from "lucide-react";
import { easeExpo } from "../../../lib/motion";

interface FineTunePanelProps {
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/**
 * The manual controls, demoted behind a toggle. Talking to Iris is the default;
 * Fine-tune opens the brand/asset/variant controls and the composed-prompt card
 * for operators who want to drive a single asset by hand. Closed by default so
 * the controls are available without dominating the screen.
 */
export function FineTunePanel({ open, onToggle, children }: FineTunePanelProps) {
  const reduce = useReducedMotion();

  return (
    <div className="glass overflow-hidden rounded-2xl shadow-panel">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-chrome-hover/50"
      >
        <SlidersHorizontal size={14} className="shrink-0 text-accent" />
        <span className="font-display text-sm font-medium tracking-tight text-text">
          Fine-tune
        </span>
        <span className="text-xs text-text-faint">manual controls</span>
        <ChevronDown
          size={16}
          className={`ml-auto shrink-0 text-text-faint transition-transform duration-200 ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={reduce ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={easeExpo}
            className="overflow-hidden"
          >
            <div className="space-y-3 border-t border-white/8 p-3">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
