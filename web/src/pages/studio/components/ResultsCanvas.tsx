import { motion } from "framer-motion";
import {
  Images,
  KeyRound,
  AlertTriangle,
  Sparkles,
  CheckCircle2,
  Layers,
  Loader2,
} from "lucide-react";
import { GlassPanel } from "../../../components/GlassPanel";
import { EmptyState } from "../../../components/EmptyState";
import { Spinner } from "../../../components/Spinner";
import { staggerContainer, staggerItem } from "../../../lib/motion";
import { ResultCard } from "./ResultCard";
import { PlannedPackList } from "./PlannedPackList";
import type { GenerateResult } from "../../../lib/types";
import type { PlannedPack } from "../lib/pack";
import {
  BusinessCardPreview,
  LinkPreviewCard,
  PhoneHomePreview,
  TabPreview,
} from "../../../components/Mockups";

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

/**
 * The generating skeleton: layout-matched placeholder tiles in the same grid the
 * staging wall uses, so the premium reveal lands into a slot that was already
 * reserved (no spinner-then-jump). Each tile is the same square frame and rounded
 * card chrome as a ResultCard, with the shared `.skeleton` shimmer. Reduced
 * motion is handled by the `.skeleton` class (it stills its own shimmer).
 */
function GeneratingSkeleton() {
  return (
    <div className="space-y-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-display text-sm font-semibold text-text">Staging wall</span>
        <span className="inline-flex items-center gap-1 text-xs text-text-muted">
          <Loader2 size={13} className="animate-spin text-accent" />
          developing
        </span>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="flex flex-col rounded-2xl border border-white/8 bg-chrome-panel/80 p-2 shadow-panel backdrop-blur"
          >
            <div
              className="skeleton w-full rounded-xl border border-white/8"
              style={{ aspectRatio: "1 / 1" }}
            />
            <div className="mt-2 flex items-center justify-between gap-2 px-0.5">
              <div className="skeleton h-3 w-20 rounded" />
              <div className="skeleton h-3 w-8 rounded" />
            </div>
          </div>
        ))}
      </div>
      <p className="text-center text-sm text-text-muted">
        Generating. This can take a moment.
      </p>
    </div>
  );
}

/**
 * The "this asset needs a model backend" panel. Only card assets (og_image,
 * social_card) compose without a key; this asset type renders with a model, so
 * it needs a provider key. The prompt above is already composed and on brand, so
 * it will run unchanged the moment a key (or the suite gateway) is configured.
 */
function NeedsKey({ backend, detail }: { backend: string; detail: string }) {
  return (
    <EmptyState
      icon={<KeyRound size={28} />}
      title={`This asset needs the ${backend} backend`}
      description={
        <>
          {detail ||
            "This asset type renders with an image model, so it needs a provider key."}{" "}
          Set a key for {backend}, or point the suite image gateway, and the
          composed prompt above runs unchanged. Card assets (OG image, social
          card) compose on brand without any key.
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
        <span className="tabular-nums">{n}</span>{" "}
        {n === 1 ? "asset" : "assets"}
      </span>
    );
  } else if (state.status === "pack_plan") {
    const n = state.plan.items.length;
    title = "Planned pack";
    badge = (
      <span className="inline-flex items-center gap-1 text-xs text-text-muted">
        <Layers size={14} className="text-accent" />
        <span className="tabular-nums">{n}</span>{" "}
        {n === 1 ? "asset" : "assets"}
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

  // The idle/empty canvas gets the faint dot-grid so the hero area reads as a
  // surface waiting to be filled, not a blank box.
  const showDotGrid = state.status === "idle";

  return (
    <GlassPanel
      title={
        <span className="font-display tracking-tight">{title}</span>
      }
      actions={badge}
      flush
      glow={state.status === "results"}
      className="flex min-h-[40vh] flex-col lg:h-full lg:min-h-0"
    >
      <div
        className={`min-h-0 flex-1 overflow-y-auto p-4 ${
          showDotGrid ? "bg-dot-grid bg-dot-grid" : ""
        }`}
      >
        {state.status === "idle" && (
          <EmptyState
            icon={<Sparkles size={28} />}
            title="Results appear here"
            description="Compose a prompt on the left, then Generate. Each run can return several versions to pick from."
          />
        )}

        {state.status === "generating" && <GeneratingSkeleton />}

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
              <div className="space-y-4">
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-display text-sm font-semibold text-text">Staging wall</span>
                    <span className="text-xs text-text-faint">compliance stamps use returned warnings/missing refs</span>
                  </div>
                  <motion.div
                    variants={staggerContainer}
                    initial="hidden"
                    animate="show"
                    className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3"
                  >
                    {state.result.files.map((file, i) => (
                      <motion.div key={file.path} variants={staggerItem}>
                        <ResultCard
                          result={state.result}
                          file={file}
                          index={i + 1}
                          selected={selectedIndex === i}
                          onSelect={() => onSelect(i)}
                          onVersion={onVersion}
                        />
                      </motion.div>
                    ))}
                  </motion.div>
                </div>
                <div className="rounded-2xl border border-white/8 bg-chrome-panel/70 p-3">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="font-display text-sm font-semibold text-text">Try-on strip</span>
                    <span className="text-xs text-text-faint">previews use the generated image</span>
                  </div>
                  <div className="flex gap-3 overflow-x-auto pb-1">
                    <PhoneHomePreview icon={state.result.files[0]?.url ?? undefined} label={state.result.brand} />
                    <TabPreview favicon={state.result.files[0]?.url ?? undefined} title={state.result.brand} domain={`${state.result.brand}.brand`} />
                    <LinkPreviewCard ogImage={state.result.files[0]?.url ?? undefined} domain={`${state.result.brand}.brand`} title={`${state.result.brand} ${state.result.asset}`} description="Generated in Brand-Ops Studio." />
                    <BusinessCardPreview logo={state.result.files[0]?.url ?? undefined} name={state.result.brand} tagline={state.result.variant} />
                  </div>
                </div>
              </div>
            </>
          ))}
      </div>
    </GlassPanel>
  );
}
