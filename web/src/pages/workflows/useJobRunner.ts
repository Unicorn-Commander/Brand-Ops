/**
 * The run queue for the workflows page.
 *
 * Each card hands this hook an agent name and a payload; the hook posts to
 * api.runAgent, records the run in the jobs list, and flips it to done (with the
 * agent's AgentRunResult) or error (with a message) when the call settles. There
 * is no server-side job queue to poll: a run is a single request, so the only
 * states are running -> done / error.
 *
 * The agents degrade gracefully: a missing LLM gateway or Brigade hub still
 * returns HTTP 200 with the agent's own `status` set to something like "skipped"
 * or "research_only". Those are NOT errors here; the result panel reads the
 * agent status and labels it. `error` is reserved for the request failing.
 */
import { useCallback, useRef, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { AgentRunResult } from "../../lib/types";
import type { AgentName, Job, WorkflowDef } from "./types";

/** Inputs needed to start a run. */
interface StartArgs {
  workflow: WorkflowDef;
  /** The payload sent to api.runAgent (the agent's own input shape). */
  payload: Record<string, unknown>;
  /** A short one-line description of the inputs, shown on the job row. */
  inputSummary: string;
  /** Page-local context stashed on the job, not sent to the agent. */
  basePrompt?: string;
}

function makeId(): string {
  return `run_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export interface JobRunner {
  jobs: Job[];
  /**
   * Start a run. Returns a promise that resolves with the agent result when the
   * call succeeds (so a card can chain, e.g. critique -> refine), or rejects
   * with the error when it fails. The jobs list is updated either way.
   */
  startRun: (args: StartArgs) => Promise<AgentRunResult>;
  dismiss: (jobId: string) => void;
  clearDone: () => void;
}

export function useJobRunner(): JobRunner {
  const [jobs, setJobs] = useState<Job[]>([]);
  // Mirror so callbacks never need the latest jobs as a dependency.
  const jobsRef = useRef<Job[]>([]);
  jobsRef.current = jobs;

  const startRun = useCallback(
    async (args: StartArgs): Promise<AgentRunResult> => {
      const id = makeId();
      const agent: AgentName = args.workflow.agent;
      const job: Job = {
        id,
        agent,
        workflowLabel: args.workflow.label,
        inputSummary: args.inputSummary,
        status: "running",
        createdAt: Date.now(),
        basePrompt: args.basePrompt,
      };
      // Newest first.
      setJobs((prev) => [job, ...prev]);

      try {
        const result = await api.runAgent(agent, args.payload);
        setJobs((prev) =>
          prev.map((j) =>
            j.id === id ? { ...j, status: "done", result } : j,
          ),
        );
        return result;
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "The run failed.";
        setJobs((prev) =>
          prev.map((j) =>
            j.id === id ? { ...j, status: "error", error: message } : j,
          ),
        );
        throw err;
      }
    },
    [],
  );

  const dismiss = useCallback((jobId: string) => {
    setJobs((prev) => prev.filter((j) => j.id !== jobId));
  }, []);

  const clearDone = useCallback(() => {
    setJobs((prev) => prev.filter((j) => j.status === "running"));
  }, []);

  return { jobs, startRun, dismiss, clearDone };
}
