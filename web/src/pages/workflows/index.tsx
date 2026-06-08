/**
 * Workflows: the agent-workflow runner.
 *
 * Each card on this page runs a real Brand-Ops agent through api.runAgent: plan
 * an asset pack or avatar set, critique a saved asset against its brand, refine
 * a prompt from a critique, or research a brand. A run is a single request (no
 * server-side queue), so it shows as Running, then Done with the agent's
 * structured output, or Failed. The agents degrade gracefully: a missing LLM
 * gateway or Brigade hub still returns 200 with a status like "skipped" or
 * "research_only", which the result panel labels rather than treating as an
 * error.
 *
 * This is the on-ramp to the suite-wide chat, so the language stays plain.
 */
import { useEffect, useRef, useState } from "react";
import { MessagesSquare } from "lucide-react";
import { useActiveBrand } from "../../brand-context";
import { useJobRunner } from "./useJobRunner";
import { JobsPanel } from "./components/JobsPanel";
import { AssetPackCard } from "./components/cards/AssetPackCard";
import { AvatarCard } from "./components/cards/AvatarCard";
import { CriticCard } from "./components/cards/CriticCard";
import { RefineCard, type RefineSeed } from "./components/cards/RefineCard";
import { ResearchCard } from "./components/cards/ResearchCard";
import type { StartRunArgs } from "./components/cards/shared";
import type { AgentRunResult } from "../../lib/types";
import type { AgentName, CriticResult } from "./types";

export default function WorkflowsPage() {
  const { activeBrand, activeSlug } = useActiveBrand();
  const { jobs, startRun, dismiss, clearDone } = useJobRunner();
  const activeName = activeBrand?.display_name ?? null;

  // Job ids whose result panel is expanded.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  // Track which jobs already auto-expanded, so a user who collapses a result is
  // not fought by the effect re-opening it.
  const autoExpandedRef = useRef<Set<string>>(new Set());

  // Which agents currently have a run in flight (per-card loading state).
  const [busy, setBusy] = useState<Set<AgentName>>(new Set());

  // The seed pushed into the Refine card by "Send tweaks to Refine".
  const [refineSeed, setRefineSeed] = useState<RefineSeed | null>(null);
  const refineNonce = useRef(0);

  // When a run settles, reveal its result once.
  useEffect(() => {
    const newlySettled = jobs.filter(
      (j) => j.status !== "running" && !autoExpandedRef.current.has(j.id),
    );
    if (newlySettled.length === 0) return;
    setExpandedIds((prev) => {
      const next = new Set(prev);
      for (const j of newlySettled) {
        next.add(j.id);
        autoExpandedRef.current.add(j.id);
      }
      return next;
    });
  }, [jobs]);

  const toggleExpanded = (jobId: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });

  // Wrap the runner so each card can show its own loading state. The returned
  // promise is the real run (so a caller could await it), but cards fire it and
  // forget; the failure is already recorded on the job row, so a detached no-op
  // catch keeps a rejected run from surfacing as an unhandled rejection.
  const start = (args: StartRunArgs): Promise<AgentRunResult> => {
    const agent = args.workflow.agent;
    setBusy((prev) => new Set(prev).add(agent));
    const run = startRun(args).finally(() => {
      setBusy((prev) => {
        const next = new Set(prev);
        next.delete(agent);
        return next;
      });
    });
    void run.catch(() => undefined);
    return run;
  };

  const handleRefine = (basePrompt: string, critique: CriticResult) => {
    refineNonce.current += 1;
    setRefineSeed({ basePrompt, critique, nonce: refineNonce.current });
    // Bring the Refine card into view so the seed is obvious.
    document
      .getElementById("refine-card")
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div className="space-y-6">
      {/* Conversation-friendly intro: this is the on-ramp to suite-wide chat. */}
      <section className="rounded-xl border border-chrome-border bg-chrome-panel p-5 shadow-panel">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
            <MessagesSquare size={20} />
          </span>
          <div>
            <h1 className="text-base font-semibold text-text">Workflows</h1>
            <p className="mt-1 max-w-2xl text-sm text-text-muted">
              Hand a task to an agent. Each card runs a real agent and shows its
              result below.
              {activeBrand
                ? ` Brand-scoped runs use ${activeBrand.display_name}.`
                : ""}{" "}
              These will fold into the suite-wide chat.
            </p>
          </div>
        </div>
      </section>

      {/* Workflow cards. Each owns its config and runs its agent. */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-text">Start a workflow</h2>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <AssetPackCard
            startRun={start}
            running={busy.has("asset_pack")}
            activeSlug={activeSlug}
            activeName={activeName}
          />
          <AvatarCard
            startRun={start}
            running={busy.has("avatar")}
            activeSlug={activeSlug}
            activeName={activeName}
          />
          <CriticCard
            startRun={start}
            running={busy.has("brand_critic")}
            activeSlug={activeSlug}
            activeName={activeName}
          />
          <div id="refine-card">
            <RefineCard
              startRun={start}
              running={busy.has("refine")}
              seed={refineSeed}
            />
          </div>
          <ResearchCard startRun={start} running={busy.has("brand_research")} />
        </div>
      </section>

      {/* Runs. */}
      <JobsPanel
        jobs={jobs}
        expandedIds={expandedIds}
        onToggle={toggleExpanded}
        onDismiss={dismiss}
        onClearDone={clearDone}
        onRefine={handleRefine}
      />
    </div>
  );
}
