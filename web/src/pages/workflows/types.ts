/**
 * Local types for the agent-workflow runner.
 *
 * Each workflow card on this page wires to a real agent run via api.runAgent.
 * The agent return shapes are per-agent and mostly untyped on the wire (the API
 * passes the agent's own result dict through verbatim under AgentRunResult.result),
 * so these types describe what this page reads out of those dicts. They are kept
 * screen-local on purpose: they mirror the Python agents in
 * src/brand_ops/agents/* rather than a shared wire contract.
 */
import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";
import type { AgentRunResult } from "../../lib/types";

/**
 * The lifecycle states a run moves through. `running` covers both the queued
 * moment and the in-flight request (a single network call, no server-side
 * queue to poll). `done` means the agent returned; read the agent's own
 * `status` field for ok/skipped/error/etc. `error` means the call itself
 * failed (network, 4xx/5xx) before any agent result came back.
 */
export type JobStatus = "running" | "done" | "error";

/** The agent names this page can run. Matches the Brigade registry names. */
export type AgentName =
  | "asset_pack"
  | "brand_critic"
  | "refine"
  | "avatar"
  | "brand_research";

/** Static description of one workflow card. */
export interface WorkflowDef {
  /** Stable id, used as the React key and to look the card up. */
  id: AgentName;
  /** The agent name passed to api.runAgent. */
  agent: AgentName;
  label: string;
  /** One plain sentence of what this run produces. */
  summary: string;
  /** The concrete steps the agent will take, shown on the card. */
  steps: string[];
  /** Lucide icon component for the card and job rows. */
  icon: ComponentType<LucideProps>;
  /** Label for the primary action, e.g. "Run asset pack". */
  runLabel: string;
}

/** One run in the jobs panel. */
export interface Job {
  /** Client-side id. */
  id: string;
  agent: AgentName;
  /** Snapshot of the workflow label at submit time. */
  workflowLabel: string;
  /** A short one-line summary of the inputs this run used, for the row. */
  inputSummary: string;
  status: JobStatus;
  /** Epoch ms when the run was started. */
  createdAt: number;
  /** The agent result, once the call resolves. */
  result?: AgentRunResult;
  /** The error message, when status is "error". */
  error?: string;
  /**
   * Page-local context not part of the agent result. The critique run stashes
   * the prompt it judged here so the result panel can hand it to Refine (the
   * critic's own result does not echo the prompt).
   */
  basePrompt?: string;
}

// ---------------------------------------------------------------------------
// Agent result shapes (read out of AgentRunResult.result).
//
// These describe the keys this page renders. Every field is optional because
// the agents stamp different keys per status (ok / skipped / error / etc.).
// ---------------------------------------------------------------------------

/** One planned/generated item from asset_pack and avatar runs. */
export interface PackItemResult {
  asset_type?: string;
  variant?: string | null;
  prompt?: string;
  planned_path?: string;
  paths?: string[];
  status?: string;
  error?: string;
}

/** asset_pack / avatar `result`. */
export interface PlanResult {
  status?: string;
  error?: string;
  brand?: string;
  pack?: string;
  style?: string | null;
  count?: number;
  dry_run?: boolean;
  items?: PackItemResult[];
}

/** The inner object brand_critic returns under `critique`. */
export interface CritiqueBody {
  on_brand?: boolean;
  score?: number;
  reasons?: string[];
  prompt_tweaks?: string[];
}

/** brand_critic `result`. */
export interface CriticResult {
  status?: string;
  error?: string;
  note?: string;
  raw?: string;
  critique?: CritiqueBody;
}

/** refine `result`. */
export interface RefineResult {
  status?: string;
  error?: string;
  note?: string;
  refined_prompt?: string;
  base_prompt?: string;
  applied_tweaks?: string[];
  smoothed?: boolean;
}

/** brand_research `result`. Mostly used to surface the Brigade-needed status. */
export interface ResearchResult {
  status?: string;
  error?: string;
  note?: string;
  company?: string;
  domain?: string;
  research_status?: string;
  research?: Record<string, unknown>;
  profile?: Record<string, unknown>;
  brand_yaml?: Record<string, unknown>;
}
