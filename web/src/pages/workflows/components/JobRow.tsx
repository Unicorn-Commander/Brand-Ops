import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { findWorkflow } from "../data";
import { StatusPill } from "./StatusPill";
import { RunResult } from "./RunResult";
import type { CriticResult, Job } from "../types";

interface JobRowProps {
  job: Job;
  /** Whether the result panel is expanded. */
  expanded: boolean;
  /** Toggle the result panel (only meaningful once settled). */
  onToggle: () => void;
  /** Dismiss this run from the list. */
  onDismiss: () => void;
  /** Forwarded to the critique result so a user can chain into Refine. */
  onRefine?: (basePrompt: string, critique: CriticResult) => void;
}

/** Short relative time like "just now" / "2m ago". Plain, no library needed. */
function relativeTime(then: number, now: number): string {
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return `${h}h ago`;
}

/**
 * One run in the jobs panel: the workflow icon, its label and the input it ran
 * with, a status pill, and (once settled) an expandable result panel rendering
 * the agent's structured output.
 */
export function JobRow({
  job,
  expanded,
  onToggle,
  onDismiss,
  onRefine,
}: JobRowProps) {
  const def = findWorkflow(job.agent);
  const Icon = def?.icon;
  const running = job.status === "running";
  const settled = !running;

  // Keep the relative time fresh while the run is in flight.
  const [, force] = useState(0);
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => force((n) => n + 1), 1000);
    return () => window.clearInterval(t);
  }, [running]);

  return (
    <div className="border-b border-chrome-border last:border-b-0">
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-chrome-border bg-chrome-raised text-text-muted">
          {Icon ? <Icon size={16} /> : null}
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-text">
              {job.workflowLabel}
            </p>
            {job.inputSummary && (
              <span className="truncate text-xs text-text-faint">
                {job.inputSummary}
              </span>
            )}
          </div>

          {/* An indeterminate accent bar while running; quiet once settled. */}
          {running ? (
            <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-chrome-raised">
              <div className="h-full w-1/3 animate-pulse rounded-full bg-accent" />
            </div>
          ) : (
            <p className="mt-0.5 text-xs text-text-faint">
              {relativeTime(job.createdAt, Date.now())}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-3">
          <StatusPill status={job.status} />
          {settled ? (
            <button
              type="button"
              onClick={onToggle}
              aria-expanded={expanded}
              aria-label={expanded ? "Hide result" : "Show result"}
              className="rounded-lg p-1 text-text-muted hover:bg-chrome-hover hover:text-text"
            >
              <ChevronRight
                size={16}
                className={`transition-transform ${expanded ? "rotate-90" : ""}`}
              />
            </button>
          ) : (
            <span className="w-[26px]" aria-hidden="true" />
          )}
        </div>
      </div>

      {settled && expanded && (
        <div className="px-4 pb-4">
          <RunResult job={job} onDismiss={onDismiss} onRefine={onRefine} />
        </div>
      )}
    </div>
  );
}
