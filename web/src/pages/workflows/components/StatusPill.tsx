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
  { label: string; className: string; icon: LucideIcon; spin?: boolean }
> = {
  running: {
    label: "Running",
    className: "border-accent/40 bg-accent/10 text-accent",
    icon: Loader2,
    spin: true,
  },
  done: {
    label: "Done",
    className: "border-accent/40 bg-accent/15 text-accent",
    icon: CircleCheck,
  },
  error: {
    label: "Failed",
    className: "border-red-500/40 bg-red-500/15 text-red-300",
    icon: CircleAlert,
  },
};

/**
 * The status badge for a run row. Running animates, done picks up the brand
 * accent, failed is red.
 */
export function StatusPill({ status }: { status: JobStatus }) {
  const { label, className, icon: Icon, spin } = STYLES[status];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${className}`}
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
    className = "border-accent/40 bg-accent/15 text-accent";
    Icon = CircleCheck;
  } else if (normalized === "error") {
    className = "border-red-500/40 bg-red-500/15 text-red-300";
    Icon = CircleAlert;
  } else if (WARN_STATUSES.has(normalized)) {
    className = "border-amber-500/40 bg-amber-500/15 text-amber-200";
    Icon = CircleSlash;
  } else {
    className = "border-chrome-border bg-chrome-raised text-text-muted";
    Icon = CircleSlash;
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${className}`}
    >
      <Icon size={13} />
      {status}
    </span>
  );
}
