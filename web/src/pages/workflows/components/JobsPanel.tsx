import { ListChecks } from "lucide-react";
import { Panel } from "../../../components/Panel";
import { EmptyState } from "../../../components/EmptyState";
import { Button } from "../../../components/Button";
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
 * first. Empty until the first run is started.
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
    <Panel
      title="Runs"
      flush={jobs.length > 0}
      actions={
        <div className="flex items-center gap-3">
          {active > 0 && (
            <span className="text-xs text-text-muted">{active} running</span>
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
          {jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              expanded={expandedIds.has(job.id)}
              onToggle={() => onToggle(job.id)}
              onDismiss={() => onDismiss(job.id)}
              onRefine={onRefine}
            />
          ))}
        </div>
      )}
    </Panel>
  );
}
