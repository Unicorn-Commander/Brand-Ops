/**
 * The result panel for a finished (or failed) run.
 *
 * It dispatches on the agent name to render that agent's structured output:
 * a planned pack/avatar list, a critique, a refined prompt, or a research
 * profile. Every agent also stamps its own `status`; that is surfaced as a pill
 * so a graceful "skipped" (no LLM) or "research_only" (no Brigade) reads as a
 * clear state rather than a failure.
 *
 * For the on-brand critique, the rendered critique is also offered to the
 * Refine card via `onRefine`, closing the critique -> refine loop on this page.
 */
import { X } from "lucide-react";
import { Button } from "../../../components/Button";
import { AgentStatusPill } from "./StatusPill";
import { BulletList, CodeBlock, MetaRow, Notice } from "./ui";
import type {
  CriticResult,
  Job,
  PlanResult,
  RefineResult,
  ResearchResult,
} from "../types";

interface RunResultProps {
  job: Job;
  onDismiss: () => void;
  /**
   * Offered to the critique result so a user can send the critique straight to
   * the Refine card. Receives the asset prompt the critique judged and the
   * critique result object.
   */
  onRefine?: (basePrompt: string, critique: CriticResult) => void;
}

export function RunResult({ job, onDismiss, onRefine }: RunResultProps) {
  // A failed request (not an agent "skipped").
  if (job.status === "error") {
    return (
      <ResultShell onDismiss={onDismiss}>
        <Notice tone="error" title="The run did not complete">
          {job.error ?? "The request failed."}
        </Notice>
      </ResultShell>
    );
  }

  const result = job.result?.result ?? {};
  const agentStatus = job.result?.status ?? "unknown";

  let body: React.ReactNode;
  switch (job.agent) {
    case "asset_pack":
    case "avatar":
      body = <PlanResultView result={result as PlanResult} />;
      break;
    case "brand_critic":
      body = (
        <CritiqueResultView
          result={result as CriticResult}
          basePrompt={readCritiqueBasePrompt(job)}
          onRefine={onRefine}
        />
      );
      break;
    case "refine":
      body = <RefineResultView result={result as RefineResult} />;
      break;
    case "brand_research":
      body = <ResearchResultView result={result as ResearchResult} />;
      break;
    default:
      body = <RawResultView result={result} />;
  }

  return (
    <ResultShell onDismiss={onDismiss} statusPill={<AgentStatusPill status={agentStatus} />}>
      {body}
    </ResultShell>
  );
}

/** The shared frame: a header with the agent status pill and a dismiss button. */
function ResultShell({
  children,
  onDismiss,
  statusPill,
}: {
  children: React.ReactNode;
  onDismiss: () => void;
  statusPill?: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-chrome-border bg-chrome-bg/30 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-text-muted">Result</span>
          {statusPill}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="-mr-1 -mt-1 rounded-lg p-1 text-text-muted hover:bg-chrome-hover hover:text-text"
        >
          <X size={16} />
        </button>
      </div>
      <div className="space-y-3">{children}</div>
      <div className="mt-3 flex justify-end">
        <Button variant="ghost" size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// asset_pack / avatar
// ---------------------------------------------------------------------------

function PlanResultView({ result }: { result: PlanResult }) {
  if (result.status === "error") {
    return (
      <Notice tone="error" title="The agent reported an error">
        {result.error ?? "The plan could not be built."}
      </Notice>
    );
  }
  const items = result.items ?? [];
  const dryRun = result.dry_run !== false;
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5">
        {result.brand && <MetaRow label="brand" value={result.brand} />}
        {result.pack && <MetaRow label="pack" value={result.pack} />}
        {result.style != null && result.style !== "" && (
          <MetaRow label="style" value={result.style} />
        )}
        <MetaRow label="items" value={String(result.count ?? items.length)} />
        <MetaRow label="mode" value={dryRun ? "dry run (planned)" : "generated"} />
      </dl>

      {items.length === 0 ? (
        <p className="text-sm text-text-muted">The plan produced no items.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <li
              key={`${i}-${item.asset_type}-${item.variant ?? ""}`}
              className="rounded-lg border border-chrome-border bg-chrome-raised/40 p-3"
            >
              <div className="flex items-center justify-between gap-3">
                <p className="truncate text-sm font-medium text-text">
                  {[item.asset_type, item.variant].filter(Boolean).join(" / ") ||
                    item.asset_type ||
                    "asset"}
                </p>
                {item.status && <AgentStatusPill status={item.status} />}
              </div>
              {item.error ? (
                <p className="mt-1.5 text-xs text-red-300">{item.error}</p>
              ) : (
                item.prompt && (
                  <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-text-muted">
                    {item.prompt}
                  </p>
                )
              )}
              {item.planned_path && (
                <p
                  className="mt-1 truncate font-mono text-[11px] text-text-faint"
                  title={item.planned_path}
                >
                  {item.planned_path}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// brand_critic
// ---------------------------------------------------------------------------

function CritiqueResultView({
  result,
  basePrompt,
  onRefine,
}: {
  result: CriticResult;
  basePrompt: string;
  onRefine?: (basePrompt: string, critique: CriticResult) => void;
}) {
  if (result.status === "error") {
    return (
      <Notice tone="error" title="The agent reported an error">
        {result.error ?? "The critique could not run."}
      </Notice>
    );
  }
  if (result.status === "skipped") {
    return (
      <Notice tone="warn" title="Critique did not run">
        {result.note ?? "The LLM gateway is not configured, so the critic was skipped."}
      </Notice>
    );
  }
  if (result.status === "unparsed") {
    return (
      <div className="space-y-3">
        <Notice tone="warn" title="The model reply could not be parsed">
          The critic ran but its answer was not valid JSON. The raw reply is below.
        </Notice>
        {result.raw && <CodeBlock label="Raw reply">{result.raw}</CodeBlock>}
      </div>
    );
  }

  const critique = result.critique ?? {};
  const onBrand = critique.on_brand === true;
  const score = typeof critique.score === "number" ? critique.score : null;
  const tweaks = critique.prompt_tweaks ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-medium ${
            onBrand
              ? "border-accent/40 bg-accent/15 text-accent"
              : "border-amber-500/40 bg-amber-500/15 text-amber-200"
          }`}
        >
          {onBrand ? "On-brand" : "Off-brand"}
        </span>
        {score != null && (
          <span className="text-sm text-text-muted">
            Score <span className="font-semibold text-text">{score}</span> / 100
          </span>
        )}
      </div>

      <BulletList label="Reasons" items={critique.reasons ?? []} />
      <BulletList label="Prompt tweaks" items={tweaks} accent />

      {onRefine && tweaks.length > 0 && (
        <div className="flex justify-start pt-1">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onRefine(basePrompt, result)}
          >
            Send tweaks to Refine
          </Button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// refine
// ---------------------------------------------------------------------------

function RefineResultView({ result }: { result: RefineResult }) {
  if (result.status === "error") {
    return (
      <Notice tone="error" title="The agent reported an error">
        {result.error ?? "The prompt could not be refined."}
      </Notice>
    );
  }
  const applied = result.applied_tweaks ?? [];
  return (
    <div className="space-y-3">
      {applied.length === 0 && result.note ? (
        <Notice tone="info" title="No applicable tweaks">
          {result.note}
        </Notice>
      ) : (
        <BulletList label="Tweaks applied" items={applied} accent />
      )}
      {result.refined_prompt && (
        <CodeBlock label="Refined prompt">{result.refined_prompt}</CodeBlock>
      )}
      {result.base_prompt && result.base_prompt !== result.refined_prompt && (
        <CodeBlock label="Base prompt">{result.base_prompt}</CodeBlock>
      )}
      {result.smoothed && (
        <p className="text-xs text-text-faint">
          Smoothed into natural language by the model.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// brand_research
// ---------------------------------------------------------------------------

function ResearchResultView({ result }: { result: ResearchResult }) {
  if (result.status === "error") {
    return (
      <Notice tone="error" title="The agent reported an error">
        {result.error ?? "Research could not run."}
      </Notice>
    );
  }

  const brigadeNote =
    result.research_status ??
    (result.research ? null : "The Brigade research hub returned no context.");

  // Without Brigade the agent comes back research_only (no LLM) or ok with an
  // empty profile. Either way, surface the Brigade dependency clearly.
  if (result.status === "research_only" || (!result.profile && brigadeNote)) {
    return (
      <div className="space-y-3">
        <Notice tone="warn" title="Needs the Brigade research hub">
          {result.note ??
            "Brand onboarding delegates web research to the Brigade hub, which is not available here. Connect Brigade (and a strong model) to synthesize a profile."}
        </Notice>
        {brigadeNote && (
          <dl className="grid grid-cols-1 gap-y-1.5">
            <MetaRow label="research" value={brigadeNote} />
            {result.company && <MetaRow label="company" value={result.company} />}
            {result.domain && <MetaRow label="domain" value={result.domain} />}
          </dl>
        )}
      </div>
    );
  }

  if (result.status === "unparsed") {
    return (
      <Notice tone="warn" title="The model reply could not be parsed">
        Research ran but the synthesized profile was not valid JSON.
      </Notice>
    );
  }

  // The full happy path needs both Brigade and a strong model. Render the
  // synthesized profile as JSON when present.
  return (
    <div className="space-y-3">
      {brigadeNote && (
        <Notice tone="info" title="Brigade research">
          {brigadeNote}
        </Notice>
      )}
      {result.profile && (
        <CodeBlock label="Synthesized profile">
          {JSON.stringify(result.profile, null, 2)}
        </CodeBlock>
      )}
      {result.brand_yaml && (
        <CodeBlock label="Draft brand.yaml">
          {JSON.stringify(result.brand_yaml, null, 2)}
        </CodeBlock>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// fallback
// ---------------------------------------------------------------------------

function RawResultView({ result }: { result: Record<string, unknown> }) {
  return <CodeBlock label="Result">{JSON.stringify(result, null, 2)}</CodeBlock>;
}

/**
 * Recover the asset prompt a critique judged. The critique result does not echo
 * the prompt, so the critique card stashes it on the job as `basePrompt`.
 */
function readCritiqueBasePrompt(job: Job): string {
  return typeof job.basePrompt === "string" ? job.basePrompt : "";
}
