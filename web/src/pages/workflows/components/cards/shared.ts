/**
 * Shared types for the workflow cards.
 *
 * Every card is handed `startRun` from the page (which wraps the job runner)
 * and the active brand. The cards own their own inputs because the agents take
 * very different payloads.
 */
import type { AgentRunResult } from "../../../../lib/types";
import type { WorkflowDef } from "../../types";

/** Arguments a card passes to start a run. Mirrors the job runner's StartArgs. */
export interface StartRunArgs {
  workflow: WorkflowDef;
  /** The payload sent to api.runAgent (the agent's own input shape). */
  payload: Record<string, unknown>;
  /** A short one-line description of the inputs, shown on the job row. */
  inputSummary: string;
  /**
   * Page-local context to stash on the job (not sent to the agent). The
   * critique card uses this to carry the prompt it judged so the result can
   * flow into Refine.
   */
  basePrompt?: string;
}

/** The page-provided run starter. Resolves with the agent result. */
export type StartRun = (args: StartRunArgs) => Promise<AgentRunResult>;
