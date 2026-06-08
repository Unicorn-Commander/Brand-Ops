import {
  Images,
  KeyRound,
  AlertTriangle,
  Sparkles,
  CheckCircle2,
  Layers,
  Loader2,
} from "lucide-react";
import { Panel } from "../../../components/Panel";
import { EmptyState } from "../../../components/EmptyState";
import { Spinner } from "../../../components/Spinner";
import { ResultCard } from "./ResultCard";
import { PlannedPackList } from "./PlannedPackList";
import type { GenerateResult } from "../../../lib/types";
import type { PlannedPack } from "../lib/pack";

/** Discriminated canvas state the screen drives. */
export type CanvasState =
  | { status: "idle" }
  | { status: "planning_pack" }
  | { status: "generating" }
  | { status: "needs_key"; backend: string; detail: string }
  | { status: "error"; message: string }
  | { status: "pack_plan"; plan: PlannedPack }
  | { status: "results"; result: GenerateResult };

interface ResultsCanvasProps {
  state: CanvasState;
  /** Index of the selected version within the current result batch. */
  selectedIndex: number | null;
  onSelect: (index: number) => void;
  /** Re-run the current request for another version. */
  onVersion: () => void;
}

/** The friendly "this backend needs a key" panel. Generation is parked. */
function NeedsKey({ backend, detail }: { backend: string; detail: string }) {
  return (
    <EmptyState
      icon={<KeyRound size={28} />}
      title={`The ${backend} backend needs a key`}
      description={
        <>
          {detail || "Add a credential for this backend to generate live."} The
          prompt above is ready, so generation will run as soon as the key is in
          place.
        </>
      }
    />
  );
}

/**
 * The right-hand canvas. Shows results as a grid once a run returns, the
 * needs-key state when the backend is not configured (expected while generation
 * is parked), a spinner while generating, or an idle prompt to get started.
 */
export function ResultsCanvas({
  state,
  selectedIndex,
  onSelect,
  onVersion,
}: ResultsCanvasProps) {
  let title: React.ReactNode = "Canvas";
  let badge: React.ReactNode = null;

  if (state.status === "results") {
    const n = state.result.files.length;
    title = "Results";
    badge = (
      <span className="inline-flex items-center gap-1 text-xs text-text-muted">
        <CheckCircle2 size={14} className="text-accent" />
        {n} {n === 1 ? "asset" : "assets"}
      </span>
    );
  } else if (state.status === "pack_plan") {
    const n = state.plan.items.length;
    title = "Planned pack";
    badge = (
      <span className="inline-flex items-center gap-1 text-xs text-text-muted">
        <Layers size={14} className="text-accent" />
        {n} {n === 1 ? "asset" : "assets"}
      </span>
    );
  } else if (state.status === "planning_pack") {
    title = "Planning";
    badge = (
      <span className="inline-flex items-center gap-1 text-xs text-text-muted">
        <Loader2 size={14} className="animate-spin text-accent" />
        agent running
      </span>
    );
  }

  return (
    <Panel title={title} actions={badge} flush className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {state.status === "idle" && (
          <EmptyState
            icon={<Sparkles size={28} />}
            title="Results appear here"
            description="Compose a prompt on the left, then Generate. Each run can return several versions to pick from."
          />
        )}

        {state.status === "generating" && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Spinner size={28} />
            <p className="text-sm text-text-muted">
              Generating. This can take a moment.
            </p>
          </div>
        )}

        {state.status === "planning_pack" && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <Spinner size={28} />
            <p className="text-sm text-text-muted">
              The agent is planning the pack and composing each prompt.
            </p>
          </div>
        )}

        {state.status === "pack_plan" && <PlannedPackList plan={state.plan} />}

        {state.status === "needs_key" && (
          <NeedsKey backend={state.backend} detail={state.detail} />
        )}

        {state.status === "error" && (
          <EmptyState
            icon={<AlertTriangle size={28} />}
            title="That run did not complete"
            description={state.message}
          />
        )}

        {state.status === "results" &&
          (state.result.files.length === 0 ? (
            <EmptyState
              icon={<Images size={28} />}
              title="No files came back"
              description="The run finished but returned no assets. Try adjusting the prompt or the backend."
            />
          ) : (
            <>
              {state.result.warnings.length > 0 && (
                <ul className="mb-3 space-y-1">
                  {state.result.warnings.map((w, i) => (
                    <li
                      key={i}
                      className="flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200 ring-1 ring-amber-500/25"
                    >
                      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                      <span>{w}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {state.result.files.map((file, i) => (
                  <ResultCard
                    key={file.path}
                    result={state.result}
                    file={file}
                    index={i + 1}
                    selected={selectedIndex === i}
                    onSelect={() => onSelect(i)}
                    onVersion={onVersion}
                  />
                ))}
              </div>
            </>
          ))}
      </div>
    </Panel>
  );
}
