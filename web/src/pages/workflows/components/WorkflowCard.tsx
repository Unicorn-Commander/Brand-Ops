import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";
import { SpotlightCard } from "../../../components";
import { spring } from "../../../lib/motion";
import type { WorkflowDef } from "../types";

interface WorkflowCardProps {
  workflow: WorkflowDef;
  /** The card's config form and Run button. */
  children: ReactNode;
}

/**
 * The presentational shell for one workflow: its name, a plain line of what it
 * does, the steps the agent will take, and a slot for the card's own config
 * form and Run action. A cursor-follow spotlight and a small hover lift make
 * each capability card feel live; the icon tile uses the active brand accent so
 * the grid themes with the brand.
 */
export function WorkflowCard({ workflow, children }: WorkflowCardProps) {
  const Icon = workflow.icon;
  const reduce = useReducedMotion();
  return (
    <motion.div
      whileHover={reduce ? undefined : { y: -3 }}
      transition={spring.card}
      className="h-full"
    >
      <SpotlightCard className="glass accent-ring flex h-full flex-col gap-4 rounded-2xl p-5 shadow-panel transition-shadow duration-300 hover:shadow-float">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 text-accent shadow-glow">
            <Icon size={20} />
          </span>
          <div className="min-w-0">
            <h3 className="font-display text-base font-semibold tracking-tight text-text">
              {workflow.label}
            </h3>
            <p className="mt-1 text-sm text-text-muted">{workflow.summary}</p>
          </div>
        </div>

        <ul className="space-y-1.5">
          {workflow.steps.map((step) => (
            <li
              key={step}
              className="flex items-start gap-2 text-xs text-text-muted"
            >
              <ChevronRight size={14} className="mt-0.5 shrink-0 text-accent/70" />
              <span>{step}</span>
            </li>
          ))}
        </ul>

        <div className="mt-auto space-y-3 pt-1">{children}</div>
      </SpotlightCard>
    </motion.div>
  );
}
