import {
  Loader2,
  CircleCheck,
  CircleAlert,
  CircleSlash,
  type LucideIcon,
} from "lucide-react";
import type { JobStatus } from "../types";

/** Per-run-status label + dark-chrome styling. `done` borrows the brand accent. */
const STYLES: Record<
  JobStatus,
  { label: string; className: string; icon: LucideIcon; spin?: boolean; pulse?: boolean }
> = {
  running: {
    label: "Running",
    className: "border-accent/50 bg-accent/10 text-accent",
    icon: Loader2,
    spin: true,
    pulse: true,
  },
  done: {
    label: "Done",
    className: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
    icon: CircleCheck,
  },
  error: {
    label: "Failed",
    className: "border-red-400/40 bg-red-400/10 text-red-300",
    icon: CircleAlert,
  },
};

/**
 * The status badge for a run row. Running animates with a soft accent pulse,
 * done reads as a calm green confirmation, failed is red.
 */
export function StatusPill({ status }: { status: JobStatus }) {
  const { label, className, icon: Icon, spin, pulse } = STYLES[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium tabular-nums ${className} ${pulse ? "animate-pulse" : ""}`}
    >
      <Icon size={13} className={spin ? "animate-spin" : ""} />
      {label}
    </span>
  );
}

/**
 * A smaller pill for the agent's OWN result status (the `status` field the
 * agent stamps: ok, skipped, research_only, unparsed, error, ...). This is
 * distinct from the run status: a run can be "done" while the agent says
 * "skipped" because no LLM was configured.
 */
const OK_STATUSES = new Set(["ok", "planned", "generated"]);
const WARN_STATUSES = new Set(["skipped", "research_only", "unparsed"]);

export function AgentStatusPill({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  let className: string;
  let Icon: LucideIcon;
  if (OK_STATUSES.has(normalized)) {
    className = "border-emerald-400/40 bg-emerald-400/10 text-emerald-300";
    Icon = CircleCheck;
  } else if (normalized === "error") {
    className = "border-red-400/40 bg-red-400/10 text-red-300";
    Icon = CircleAlert;
  } else if (WARN_STATUSES.has(normalized)) {
    className = "border-amber-400/40 bg-amber-400/10 text-amber-200";
    Icon = CircleSlash;
  } else {
    className = "border-white/8 bg-chrome-raised/60 text-text-muted";
    Icon = CircleSlash;
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium tabular-nums ${className}`}
    >
      <Icon size={13} />
      {status}
    </span>
  );
}
