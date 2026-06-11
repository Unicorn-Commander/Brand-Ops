import { ListChecks } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { GlassPanel } from "../../../components";
import { EmptyState } from "../../../components/EmptyState";
import { Button } from "../../../components/Button";
import { staggerItem } from "../../../lib/motion";
import { JobRow } from "./JobRow";
import type { CriticResult, Job } from "../types";

interface JobsPanelProps {
  jobs: Job[];
  /** Ids of jobs whose result panel is expanded. */
  expandedIds: ReadonlySet<string>;
  onToggle: (jobId: string) => void;
  onDismiss: (jobId: string) => void;
  /** Remove every settled run. Hidden when none are settled. */
  onClearDone: () => void;
  /** Forwarded to critique result panels so a user can chain into Refine. */
  onRefine?: (basePrompt: string, critique: CriticResult) => void;
}

/**
 * The runs panel: a live list of started agent runs with their status. Newest
 * first. New runs fade and de-blur in; empty until the first run is started.
 */
export function JobsPanel({
  jobs,
  expandedIds,
  onToggle,
  onDismiss,
  onClearDone,
  onRefine,
}: JobsPanelProps) {
  const hasSettled = jobs.some((j) => j.status !== "running");
  const active = jobs.filter((j) => j.status === "running").length;

  return (
    <GlassPanel
      title="Runs"
      flush={jobs.length > 0}
      actions={
        <div className="flex items-center gap-3">
          {active > 0 && (
            <span className="inline-flex items-center gap-1.5 text-xs text-text-muted">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-70" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-accent" />
              </span>
              <span className="tabular-nums">{active}</span> running
            </span>
          )}
          {hasSettled && (
            <Button variant="ghost" size="sm" onClick={onClearDone}>
              Clear settled
            </Button>
          )}
        </div>
      }
    >
      {jobs.length === 0 ? (
        <EmptyState
          icon={<ListChecks size={26} />}
          title="No runs yet"
          description="Run a workflow above and it shows up here with its status and result."
        />
      ) : (
        <div>
          <AnimatePresence initial={false}>
            {jobs.map((job) => (
              <motion.div
                key={job.id}
                variants={staggerItem}
                initial="hidden"
                animate="show"
                exit={{ opacity: 0, height: 0, transition: { duration: 0.18 } }}
              >
                <JobRow
                  job={job}
                  expanded={expandedIds.has(job.id)}
                  onToggle={() => onToggle(job.id)}
                  onDismiss={() => onDismiss(job.id)}
                  onRefine={onRefine}
                />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </GlassPanel>
  );
}
