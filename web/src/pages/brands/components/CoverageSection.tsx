/**
 * Asset-coverage section for the brand sheet.
 *
 * Coverage is the worklist: of the asset types the engine can produce, which
 * does this brand already have an on-brand one of, and which are still gaps.
 * Beyond showing the gaps, this section turns each gap into an action. Two ways
 * to fill them, both explicit (every generation spends through the gateway):
 *
 *   1. Per gap: each missing asset-type row carries an inline "Generate" that
 *      runs POST /api/generate for that one type. The existing deep-link into
 *      Studio (to compose by hand) stays on the row next to it.
 *   2. Whole kit: a "Generate full kit" near the ring runs every missing type
 *      in turn, with a small live progress list under the ring.
 *
 * On any success it invalidates ["assets", slug] and ["brand", slug] so the
 * ring, the checklist, and the Library all refresh.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Sparkles, X } from "lucide-react";
import { generate, isNeedsKeyError } from "../../../api/client";
import { GlassPanel } from "../../../components/GlassPanel";
import { Button } from "../../../components/Button";
import { Spinner } from "../../../components/Spinner";
import { CoverageRing } from "../../../components/CoverageRing";
import type { BrandStats } from "../../../lib/coverage";

/** State of one type while the full kit runs. */
type KitStatus = "pending" | "working" | "done" | "failed";

/** A short, plain line for a failed generate. The needs-key 422 gets its own. */
function generateErrorText(err: unknown): string {
  if (isNeedsKeyError(err)) return "Needs a provider key.";
  if (err instanceof Error && err.message) return err.message;
  return "Generation failed.";
}

/**
 * One missing asset-type row: the label, an inline "Generate" wired to the
 * per-gap mutation, and the existing deep-link into Studio. While the row's own
 * generate is running, its button shows a spinner; a failure shows a short line
 * under the row. A row whose type already has assets that have not cleared
 * review reads "review" on its Studio link (nothing on-brand yet).
 */
function MissingRow({
  type,
  rawCount,
  working,
  error,
  onGenerate,
}: {
  type: { key: string; label: string };
  rawCount: number;
  working: boolean;
  error: unknown;
  onGenerate: (key: string) => void;
}) {
  return (
    <li className="rounded-lg border border-dashed border-amber-400/40">
      <div className="flex items-center gap-2 px-3 py-2 text-sm text-amber-300/90">
        <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-amber-400/20 text-amber-300">
          <Plus size={11} />
        </span>
        <span className="min-w-0 flex-1 truncate text-text-muted">{type.label}</span>
        <Button
          variant="ghost"
          size="sm"
          loading={working}
          onClick={() => onGenerate(type.key)}
          icon={working ? undefined : <Sparkles size={13} />}
          className="text-accent hover:text-accent"
          title={`Generate a ${type.label.toLowerCase()} for this brand`}
        >
          Generate
        </Button>
        <Link
          to="/studio"
          className="shrink-0 text-xs text-amber-300/70 hover:text-amber-300"
          title={
            rawCount > 0
              ? `Has ${rawCount}, but none have cleared review. Open Studio to review.`
              : "Compose this by hand in Studio"
          }
        >
          {rawCount > 0 ? "review" : "Studio"}
        </Link>
      </div>
      {Boolean(error) && (
        <p className="px-3 pb-2 text-xs text-red-300/90">{generateErrorText(error)}</p>
      )}
    </li>
  );
}

/** One covered asset-type row: the existing check + count display. */
function CoveredRow({
  type,
}: {
  type: { key: string; label: string; count: number; flagged: number };
}) {
  return (
    <li>
      <div className="flex items-center gap-2 rounded-lg border border-white/8 px-3 py-2 text-sm text-text-muted">
        <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-accent/20 text-[10px] text-accent">
          <Check size={11} />
        </span>
        {type.label}
        {type.flagged > 0 && (
          <span
            className="text-xs tabular-nums text-amber-300/80"
            title={`${type.flagged} of these need review`}
          >
            {type.flagged} flagged
          </span>
        )}
        <span className="ml-auto text-xs tabular-nums text-text-faint">{type.count}</span>
      </div>
    </li>
  );
}

export function CoverageSection({ slug, stats }: { slug: string; stats: BrandStats }) {
  const queryClient = useQueryClient();

  // Refresh the coverage ring (brand) and the Library (assets) after any
  // successful generate, whether one gap or the whole kit.
  function refreshCoverage() {
    void queryClient.invalidateQueries({ queryKey: ["assets", slug] });
    void queryClient.invalidateQueries({ queryKey: ["brand", slug] });
  }

  // Per-gap generate. The mutation variable IS the asset-type key, so the row
  // can show its own loading state via `gapGenerate.variables === key`.
  const gapGenerate = useMutation({
    mutationFn: (assetKey: string) => generate({ brand: slug, asset: assetKey }),
    onSuccess: () => refreshCoverage(),
  });

  // Full-kit run state: a per-type status map and whether a run is in flight.
  // Sequential (one generate at a time) to keep gateway spend legible and to
  // avoid hammering the backend; a per-type failure never aborts the rest.
  const [kit, setKit] = useState<Record<string, KitStatus>>({});
  const [kitRunning, setKitRunning] = useState(false);

  async function runFullKit() {
    const missing = stats.missing;
    if (kitRunning || missing.length === 0) return;
    setKitRunning(true);
    setKit(Object.fromEntries(missing.map((t) => [t.key, "pending" as KitStatus])));

    for (const t of missing) {
      setKit((prev) => ({ ...prev, [t.key]: "working" }));
      try {
        await generate({ brand: slug, asset: t.key });
        setKit((prev) => ({ ...prev, [t.key]: "done" }));
      } catch {
        // One failure must not stop the kit; record it and carry on.
        setKit((prev) => ({ ...prev, [t.key]: "failed" }));
      }
    }

    setKitRunning(false);
    // Refresh once at the end so the ring + Library reflect everything made.
    refreshCoverage();
  }

  const hasGaps = stats.missing.length > 0;
  // Don't show a per-gap inline error from a different gap on every row; the
  // error belongs only to the type the last per-gap mutation targeted.
  const gapErrorKey =
    gapGenerate.isError && typeof gapGenerate.variables === "string"
      ? gapGenerate.variables
      : null;
  const missingKeys = new Set(stats.missing.map((t) => t.key));

  return (
    <GlassPanel
      title="Asset coverage"
      actions={
        hasGaps ? (
          <Button
            variant="primary"
            size="sm"
            loading={kitRunning}
            onClick={runFullKit}
            icon={kitRunning ? undefined : <Sparkles size={14} />}
            title="Generate one of each missing asset type for this brand"
          >
            Generate full kit
          </Button>
        ) : undefined
      }
    >
      <div className="mb-4 flex items-center gap-4">
        <CoverageRing pct={stats.coveragePct} size={52} stroke={5} />
        <div className="min-w-0">
          <p className="text-sm text-text">
            {stats.presentTypes}/{stats.totalTypes} asset types covered
          </p>
          <p className="text-xs text-text-muted">
            {hasGaps
              ? `${stats.missing.length} ${stats.missing.length === 1 ? "type" : "types"} still to generate. Each run spends through the gateway.`
              : "Every asset type is covered."}
          </p>
        </div>
      </div>

      {/* Live progress for a full-kit run: one line per missing type. Shown only
          while a run is active or after it finishes, until the next refresh. */}
      {Object.keys(kit).length > 0 && (
        <ul className="mb-4 space-y-1 rounded-xl border border-white/8 bg-white/[0.02] p-3">
          {stats.missing
            .filter((t) => kit[t.key])
            .map((t) => {
              const s = kit[t.key];
              return (
                <li
                  key={t.key}
                  className="flex items-center gap-2 text-xs text-text-muted"
                >
                  <span className="grid h-4 w-4 shrink-0 place-items-center">
                    {s === "working" ? (
                      <Spinner size={12} />
                    ) : s === "done" ? (
                      <Check size={13} className="text-accent" />
                    ) : s === "failed" ? (
                      <X size={13} className="text-red-400" />
                    ) : (
                      <span className="h-1.5 w-1.5 rounded-full bg-text-faint/50" />
                    )}
                  </span>
                  <span className="flex-1 truncate">{t.label}</span>
                  <span
                    className={
                      s === "done"
                        ? "text-accent"
                        : s === "failed"
                          ? "text-red-300/90"
                          : "text-text-faint"
                    }
                  >
                    {s === "working"
                      ? "working"
                      : s === "done"
                        ? "done"
                        : s === "failed"
                          ? "failed"
                          : "pending"}
                  </span>
                </li>
              );
            })}
        </ul>
      )}

      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {stats.byType.map((t) =>
          missingKeys.has(t.key) ? (
            <MissingRow
              key={t.key}
              type={{ key: t.key, label: t.label }}
              rawCount={t.count}
              working={gapGenerate.isPending && gapGenerate.variables === t.key}
              error={gapErrorKey === t.key ? gapGenerate.error : null}
              onGenerate={(key) => gapGenerate.mutate(key)}
            />
          ) : (
            <CoveredRow key={t.key} type={t} />
          ),
        )}
      </ul>
    </GlassPanel>
  );
}
