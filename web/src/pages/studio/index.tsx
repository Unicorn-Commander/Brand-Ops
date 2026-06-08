import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, isNeedsKeyError } from "../../api/client";
import { applyBrandAccent } from "../../theme";
import { useActiveBrand } from "../../brand-context";
import type { BrandSummary, GenerateRequest, PromptPreview } from "../../lib/types";
import { agentTurn, userTurn, type Turn } from "./lib/transcript";
import { planFromRequest } from "./lib/agent";
import {
  inferPack,
  readPlannedItems,
  packErrorMessage,
  PACK_LABELS,
  type PackName,
} from "./lib/pack";
import { ConversationPanel } from "./components/ConversationPanel";
import { ControlsStrip, type ControlsState } from "./components/ControlsStrip";
import { PromptPreviewCard } from "./components/PromptPreviewCard";
import { ResultsCanvas, type CanvasState } from "./components/ResultsCanvas";

/** How long to wait after a control change before re-running the preview. */
const PREVIEW_DEBOUNCE_MS = 350;

/** A request is previewable once it has both a brand and an asset. */
function isReady(
  c: ControlsState,
): c is ControlsState & { brand: string; asset: string } {
  return Boolean(c.brand && c.asset);
}

/**
 * Resolve which brand a pack request is for: a brand named in the text wins,
 * otherwise the active control's brand (the app-wide selection). Returns null
 * only when there is no brand to fall back to (nothing loaded yet).
 */
function resolvePackBrand(
  text: string,
  brands: BrandSummary[],
  activeSlug: string | null,
): string | null {
  const lower = text.toLowerCase();
  for (const b of brands) {
    const name = b.display_name.toLowerCase();
    const slug = b.slug.toLowerCase().replace(/-/g, " ");
    if ((name.length > 2 && lower.includes(name)) || (slug.length > 2 && lower.includes(slug))) {
      return b.slug;
    }
  }
  return activeSlug;
}

/** Build the GenerateRequest the API expects from the current controls. */
function toRequest(
  c: ControlsState & { brand: string; asset: string },
): GenerateRequest {
  return {
    brand: c.brand,
    asset: c.asset,
    variant: c.variant,
    topic: c.topic,
    backend: c.backend,
  };
}

/**
 * Studio: the agent-first generation screen.
 *
 * Left: a conversation that drives generation (a local stub maps the request to
 * a prompt-preview call; see lib/agent.ts and the TODO below). Center: controls
 * that refine the request and re-run the preview. Right: the canvas where a
 * Generate run renders its results, or surfaces the needs-key state while
 * generation is parked.
 */
export default function StudioPage() {
  const { activeSlug, brands } = useActiveBrand();

  // Reference data. Brands come from the shared context; asset types are fetched
  // here once and cached.
  const { data: assetTypes = [] } = useQuery({
    queryKey: ["asset-types"],
    queryFn: ({ signal }) => api.listAssetTypes(signal),
  });

  // Controls. Seeded from the app's active brand and the first asset type, then
  // owned locally so the user can refine independently of the global switcher.
  const [controls, setControls] = useState<ControlsState>({
    brand: activeSlug,
    asset: null,
    variant: null,
    topic: null,
    backend: null,
  });

  // Keep the brand control in step with the global switcher until the user has
  // changed it themselves.
  const brandTouched = useRef(false);
  useEffect(() => {
    if (!brandTouched.current && activeSlug && controls.brand !== activeSlug) {
      setControls((c) => ({ ...c, brand: activeSlug }));
    }
  }, [activeSlug, controls.brand]);

  // Default the asset once asset types load.
  useEffect(() => {
    if (!controls.asset && assetTypes.length > 0) {
      setControls((c) => ({ ...c, asset: assetTypes[0].key }));
    }
  }, [assetTypes, controls.asset]);

  // Theme the content to the controls' brand (may differ from the global one).
  useEffect(() => {
    if (controls.brand) applyBrandAccent(controls.brand);
  }, [controls.brand]);

  // The selected brand's detail gives us its defined variants (prompt_assets).
  const { data: brandDetail } = useQuery({
    queryKey: ["brand", controls.brand],
    queryFn: ({ signal }) => api.getBrand(controls.brand as string, signal),
    enabled: Boolean(controls.brand),
  });

  const variantOptions = useMemo(
    () => brandDetail?.prompt_assets ?? [],
    [brandDetail],
  );

  // Backends offered across the asset types, de-duplicated and sorted.
  const backendOptions = useMemo(() => {
    const set = new Set<string>();
    for (const a of assetTypes) if (a.default_backend) set.add(a.default_backend);
    return Array.from(set).sort();
  }, [assetTypes]);

  // ---- Conversation transcript ----
  const [turns, setTurns] = useState<Turn[]>([]);
  const append = useCallback(
    (...t: Turn[]) => setTurns((prev) => [...prev, ...t]),
    [],
  );

  // ---- Prompt preview ----
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const previewAbort = useRef<AbortController | null>(null);

  const runPreview = useCallback(
    async (req: GenerateRequest, opts?: { announce?: boolean }) => {
      previewAbort.current?.abort();
      const ac = new AbortController();
      previewAbort.current = ac;
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const result = await api.promptPreview(req, ac.signal);
        setPreview(result);
        if (opts?.announce) {
          const issues = [
            ...result.missing_refs.map((r) => `missing reference: ${r}`),
            ...result.warnings,
          ];
          append(
            agentTurn(
              "prompt",
              "Prompt is ready. Review it on the right, then generate.",
              issues.length > 0 ? { details: issues } : undefined,
            ),
          );
        }
        return result;
      } catch (err) {
        if (ac.signal.aborted) return null;
        const message =
          err instanceof Error ? err.message : "Could not compose the prompt.";
        setPreviewError(message);
        if (opts?.announce) append(agentTurn("note", message, { error: true }));
        return null;
      } finally {
        if (!ac.signal.aborted) setPreviewLoading(false);
      }
    },
    [append],
  );

  // Re-run the preview when the controls change (debounced). This is the
  // "controls refine the request and re-run promptPreview" path. The first
  // preview for a fresh control set is silent; the conversation announces its
  // own previews.
  const lastReqKey = useRef<string | null>(null);
  useEffect(() => {
    if (!isReady(controls)) {
      setPreview(null);
      lastReqKey.current = null;
      return;
    }
    const req = toRequest(controls);
    const key = JSON.stringify(req);
    if (key === lastReqKey.current) return;
    lastReqKey.current = key;
    const id = window.setTimeout(() => {
      void runPreview(req);
    }, PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [controls, runPreview]);

  // ---- Canvas + agent state ----
  // The canvas is shared by the single-asset preview/generate path and the pack
  // path, so it is declared up here where both handlers can drive it.
  const [canvas, setCanvas] = useState<CanvasState>({ status: "idle" });
  const [planning, setPlanning] = useState(false);
  const packAbort = useRef<AbortController | null>(null);

  // ---- Pack path: a request for a *set* runs the asset_pack agent ----
  // When the request reads as a pack ("the launch kit", "social set", "make a
  // bunch of assets"), drive the real AssetPackAgent through api.runAgent with
  // dry_run: true and stream its planned items (asset_type + variant + composed
  // prompt) into the canvas. Generation stays gated; this shows the plan live.
  const runPack = useCallback(
    async (text: string, pack: PackName, reason: string): Promise<void> => {
      const brand = resolvePackBrand(text, brands, controls.brand);
      if (!brand) {
        append(
          agentTurn(
            "note",
            "Still loading brands. Pick a brand in the controls and resend.",
            { error: true },
          ),
        );
        return;
      }

      // Keep the brand control in step with the resolved brand.
      brandTouched.current = true;
      if (brand !== controls.brand) setControls((c) => ({ ...c, brand }));

      const packLabel = PACK_LABELS[pack] ?? `${pack} pack`;
      append(
        agentTurn("plan", "Planning a pack.", {
          details: [
            reason,
            `Brand: ${brands.find((b) => b.slug === brand)?.display_name ?? brand}.`,
            "Composing each asset's prompt without generating (dry run).",
          ],
        }),
      );

      packAbort.current?.abort();
      const ac = new AbortController();
      packAbort.current = ac;
      setCanvas({ status: "planning_pack" });

      try {
        const run = await api.runAgent(
          "asset_pack",
          { brand, pack, dry_run: true },
          ac.signal,
        );
        const plan = readPlannedItems(run, brand, pack);
        if (!plan) {
          const message = packErrorMessage(run);
          setCanvas({ status: "error", message });
          append(agentTurn("note", message, { error: true }));
          return;
        }

        setCanvas({ status: "pack_plan", plan });
        const n = plan.items.length;
        const failed = plan.items.filter((i) => i.status === "error").length;
        append(
          agentTurn(
            "pack",
            `Plan ready: ${packLabel}, ${n} ${n === 1 ? "asset" : "assets"}.` +
              (failed > 0 ? ` ${failed} could not be planned.` : ""),
            {
              details: plan.items.map(
                (i) =>
                  `${i.asset_type}${i.variant ? ` (${i.variant})` : ""}` +
                  (i.status === "error" ? " - failed" : ""),
              ),
            },
          ),
        );
      } catch (err) {
        if (ac.signal.aborted) return;
        const message =
          err instanceof Error ? err.message : "The pack planner failed.";
        setCanvas({ status: "error", message });
        append(agentTurn("note", message, { error: true }));
      }
    },
    [append, brands, controls.brand],
  );

  // ---- Single-asset path: free-text request -> controls + prompt preview ----
  const handleSend = useCallback(
    async (text: string) => {
      append(userTurn(text));
      setPlanning(true);
      append(
        agentTurn(
          "plan",
          "Reading the request and choosing the asset, brand, and direction.",
        ),
      );

      // A request for a whole set runs the pack agent instead of a single asset.
      const packIntent = inferPack(text);
      if (packIntent) {
        await runPack(text, packIntent.pack, packIntent.reason);
        setPlanning(false);
        return;
      }

      const plan = planFromRequest({
        text,
        brands,
        assetTypes,
        knownVariants: variantOptions,
        controls: {
          brand: controls.brand,
          asset: controls.asset,
          variant: controls.variant,
          topic: controls.topic,
          platform: null,
          backend: controls.backend,
        },
      });

      if (!plan) {
        append(
          agentTurn(
            "note",
            brands.length === 0 || assetTypes.length === 0
              ? "Still loading brands and asset types. Try again in a moment."
              : "I could not tell which brand or asset you meant. Set them in the controls and resend.",
            { error: true },
          ),
        );
        setPlanning(false);
        return;
      }

      // Fold the inferred fields back into the controls so they stay in sync.
      brandTouched.current = true;
      setControls((c) => ({
        ...c,
        brand: plan.request.brand,
        asset: plan.request.asset,
        variant: plan.request.variant ?? null,
        topic: plan.request.topic ?? null,
        backend: plan.request.backend ?? null,
      }));

      append(
        agentTurn("plan", "Here is the plan.", {
          details: [
            ...plan.rationale,
            ...plan.assumed.map((a) => `Assumed: ${a}`),
          ],
        }),
      );

      // Compose and announce the prompt. The controls effect would also fire,
      // but we drive it here so the conversation reflects this exact request.
      lastReqKey.current = JSON.stringify(plan.request);
      await runPreview(plan.request, { announce: true });
      setPlanning(false);
    },
    [append, brands, assetTypes, variantOptions, controls, runPreview, runPack],
  );

  // ---- Generate (single asset) ----
  const [count, setCount] = useState(1);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [generating, setGenerating] = useState(false);
  const generateAbort = useRef<AbortController | null>(null);

  const runGenerate = useCallback(async () => {
    if (!isReady(controls)) return;
    const req: GenerateRequest = { ...toRequest(controls), count };

    generateAbort.current?.abort();
    const ac = new AbortController();
    generateAbort.current = ac;
    setGenerating(true);
    setSelectedIndex(null);
    setCanvas({ status: "generating" });
    append(
      agentTurn(
        "generating",
        count > 1 ? `Generating ${count} versions.` : "Generating.",
      ),
    );

    try {
      const result = await api.generate(req, ac.signal);
      setCanvas({ status: "results", result });
      if (result.files.length > 0) setSelectedIndex(0);
      append(
        agentTurn(
          "result",
          result.files.length > 0
            ? `Done. ${result.files.length} ${
                result.files.length === 1 ? "asset" : "assets"
              } ready on the right.`
            : "The run finished but returned no files.",
        ),
      );
    } catch (err) {
      if (ac.signal.aborted) return;
      if (isNeedsKeyError(err)) {
        const { backend, detail } = err.needsKey;
        setCanvas({ status: "needs_key", backend, detail });
        append(
          agentTurn(
            "note",
            `The ${backend} backend needs a key before it can generate.`,
          ),
        );
      } else {
        const message = err instanceof Error ? err.message : "Generation failed.";
        setCanvas({ status: "error", message });
        append(agentTurn("note", message, { error: true }));
      }
    } finally {
      if (!ac.signal.aborted) setGenerating(false);
    }
  }, [controls, count, append]);

  // Abort any in-flight requests on unmount.
  useEffect(() => {
    return () => {
      previewAbort.current?.abort();
      generateAbort.current?.abort();
      packAbort.current?.abort();
    };
  }, []);

  const busy = planning || (previewLoading && !preview);

  const suggestions = useMemo(() => {
    const brandName =
      brands.find((b) => b.slug === controls.brand)?.display_name ?? "the brand";
    return [
      `Plan the launch kit for ${brandName}`,
      "Make an OG image for the investor update, dark",
      "Build the social set",
    ];
  }, [brands, controls.brand]);

  const onControlsChange = useCallback((next: ControlsState) => {
    brandTouched.current = true;
    setControls(next);
  }, []);

  return (
    <div className="grid h-[calc(100vh-7.5rem)] grid-cols-1 gap-4 lg:grid-cols-[minmax(20rem,26rem)_1fr]">
      {/* Left: conversation. */}
      <div className="min-h-0 overflow-hidden rounded-xl border border-chrome-border bg-chrome-panel shadow-panel">
        <ConversationPanel
          turns={turns}
          busy={busy}
          onSend={handleSend}
          suggestions={suggestions}
        />
      </div>

      {/* Right: controls + prompt over the results canvas. */}
      <div className="grid min-h-0 grid-rows-[auto_auto_1fr] gap-4">
        <ControlsStrip
          brands={brands}
          assetTypes={assetTypes}
          variantOptions={variantOptions}
          backendOptions={backendOptions}
          value={controls}
          onChange={onControlsChange}
          disabled={generating}
        />
        <PromptPreviewCard
          preview={preview}
          loading={previewLoading}
          error={previewError}
          count={count}
          onCountChange={setCount}
          generating={generating}
          onGenerate={runGenerate}
        />
        <div className="min-h-0">
          <ResultsCanvas
            state={canvas}
            selectedIndex={selectedIndex}
            onSelect={setSelectedIndex}
            onVersion={runGenerate}
          />
        </div>
      </div>
    </div>
  );
}
