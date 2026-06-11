import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { api, isNeedsKeyError } from "../../api/client";
import { applyBrandAccent } from "../../theme";
import { useActiveBrand } from "../../brand-context";
import { activeAgent, type Agent } from "../../lib/agents";
import type {
  BrandSummary,
  DirectorAgentRef,
  DirectorReply,
  DirectorTurn,
  GenerateRequest,
  PromptPreview,
} from "../../lib/types";
import {
  agentTurn,
  userTurn,
  type Turn,
  type TurnSpeaker,
} from "./lib/transcript";
import { planFromRequest } from "./lib/agent";
import {
  inferPack,
  readPlannedItems,
  packErrorMessage,
  PACK_LABELS,
  KNOWN_PACKS,
  DEFAULT_PACK,
  type PackName,
} from "./lib/pack";
import { ConversationPanel } from "./components/ConversationPanel";
import { CreatedAgentCard } from "./components/CreatedAgentCard";
import { IntakePanel, isNewBrandMode, type NewBrandMode } from "./components/IntakePanel";
import { ControlsStrip, type ControlsState } from "./components/ControlsStrip";
import { PromptPreviewCard } from "./components/PromptPreviewCard";
import { ResultsCanvas, type CanvasState } from "./components/ResultsCanvas";
import { StudioHeader } from "./components/StudioHeader";
import { FineTunePanel } from "./components/FineTunePanel";

/** How long to wait after a control change before re-running the preview. */
const PREVIEW_DEBOUNCE_MS = 350;

/** How many prior turns to send back to Iris as conversation context. */
const HISTORY_DEPTH = 10;

/** A request is previewable once it has both a brand and an asset. */
function isReady(
  c: ControlsState,
): c is ControlsState & { brand: string; asset: string } {
  return Boolean(c.brand && c.asset);
}

/** A pack slug, or null. Used to narrow Iris's free-form `pack` field. */
function asPackName(value: string | null): PackName | null {
  return value && (KNOWN_PACKS as readonly string[]).includes(value)
    ? (value as PackName)
    : null;
}

/**
 * Resolve which brand a pack/research request is for: a brand named in the text
 * wins, otherwise the active control's brand. Returns null only when there is no
 * brand to fall back to (nothing loaded yet).
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
 * Studio: a conversation with Iris, your brand director.
 *
 * Iris is the agent you talk to. She plans the work, delegates to the
 * specialists (research, asset_pack, the critic, the compliance gate), and
 * streams results into the canvas. When you focus a single brand you are working
 * with that brand's steward instead, scoped to that brand. The manual controls
 * (brand/asset/variant + the composed prompt) live behind a Fine-tune toggle for
 * when you want to drive one asset by hand.
 */
export default function StudioPage() {
  const { activeSlug, activeBrand, brands } = useActiveBrand();

  // The agent in the room: Iris when no brand is focused, the brand's steward
  // when one is. Driven by the app-wide active brand, not the local controls.
  const agent: Agent = useMemo(
    () => activeAgent(activeBrand),
    [activeBrand],
  );
  const speaker = useMemo<TurnSpeaker>(
    () => ({ name: agent.name, avatarUrl: agent.avatarUrl }),
    [agent],
  );

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
  // OpenRouter is always offered: it reuses the agent gateway key (one key for
  // text and images) and is the fallback the engine uses when a provider key is
  // absent. The per-asset defaults are added on top.
  const backendOptions = useMemo(() => {
    const set = new Set<string>(["openrouter"]);
    for (const a of assetTypes) if (a.default_backend) set.add(a.default_backend);
    return Array.from(set).sort();
  }, [assetTypes]);

  // ---- Conversation transcript ----
  const [turns, setTurns] = useState<Turn[]>([]);
  const append = useCallback(
    (...t: Turn[]) => setTurns((prev) => [...prev, ...t]),
    [],
  );

  // An agent turn, always stamped with the current speaker (Iris or the steward)
  // so the transcript renders the right avatar and name.
  const say = useCallback(
    (
      kind: Parameters<typeof agentTurn>[0],
      text: string,
      extra?: { details?: string[]; error?: boolean },
    ) => agentTurn(kind, text, { ...extra, speaker }),
    [speaker],
  );

  // The conversation tail, mapped for the Director endpoint (oldest first).
  const turnsRef = useRef<Turn[]>([]);
  useEffect(() => {
    turnsRef.current = turns;
  }, [turns]);
  const buildHistory = useCallback((): DirectorTurn[] => {
    return turnsRef.current
      .slice(-HISTORY_DEPTH)
      .map<DirectorTurn>((t) => ({ role: t.role, text: t.text }));
  }, []);

  // New-brand handoff: the Brands page routes here with a chosen creation mode
  // (consistent sub-brand / distinct / onboard) and we open the conversation
  // with that operation framed by Iris.
  // The active new-brand mode, read once from the handoff. When set, the Studio
  // shows the IntakePanel (a focused create-a-brand card with Iris drafting the
  // identity) instead of opening with narrated turns. Cleared when the operator
  // dismisses the panel, which returns the Studio to the normal conversation.
  const [newBrandMode, setNewBrandMode] = useState<NewBrandMode | null>(null);
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    const mode =
      typeof sessionStorage !== "undefined"
        ? sessionStorage.getItem("brand-ops.new-brand-mode")
        : null;
    if (!mode) return;
    sessionStorage.removeItem("brand-ops.new-brand-mode");
    if (isNewBrandMode(mode)) setNewBrandMode(mode);
  }, []);

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
            say(
              "prompt",
              "I composed the prompt. Open Fine-tune to review it, then generate.",
              issues.length > 0 ? { details: issues } : undefined,
            ),
          );
        }
        return result;
      } catch (err) {
        if (ac.signal.aborted) return null;
        const message =
          err instanceof Error ? err.message : "I could not compose the prompt.";
        setPreviewError(message);
        if (opts?.announce) append(say("note", message, { error: true }));
        return null;
      } finally {
        if (!ac.signal.aborted) setPreviewLoading(false);
      }
    },
    [append, say],
  );

  // Re-run the preview when the controls change (debounced). The first preview
  // for a fresh control set is silent; the conversation announces its own.
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
  // Shared by the single-asset preview/generate path and the pack path.
  const [canvas, setCanvas] = useState<CanvasState>({ status: "idle" });
  const [thinking, setThinking] = useState(false);
  const packAbort = useRef<AbortController | null>(null);
  const researchAbort = useRef<AbortController | null>(null);

  // The agent identity Iris created from the conversation (intent "agent"), shown
  // as a compact created-agent card below the hero. Cleared on dismiss and when
  // the next message is sent, so it always reflects the latest creation.
  const [agentCard, setAgentCard] = useState<DirectorAgentRef | null>(null);

  // Open Fine-tune when a single-asset prompt is composed, so the review and
  // Generate controls are in reach without burying them by default.
  const [fineTuneOpen, setFineTuneOpen] = useState(false);

  // ---- Pack path: a request for a *set* runs the asset_pack agent ----
  const runPack = useCallback(
    async (brandSlug: string | null, pack: PackName, reason: string): Promise<void> => {
      if (!brandSlug) {
        append(
          say(
            "note",
            "I am still loading your brands. Tell me the brand and I will plan it.",
            { error: true },
          ),
        );
        return;
      }

      // Keep the brand control in step with the resolved brand.
      brandTouched.current = true;
      if (brandSlug !== controls.brand) setControls((c) => ({ ...c, brand: brandSlug }));

      const packLabel = PACK_LABELS[pack] ?? `${pack} pack`;
      const brandName =
        brands.find((b) => b.slug === brandSlug)?.display_name ?? brandSlug;
      append(
        say("plan", reason, {
          details: [
            `Planning the ${packLabel.toLowerCase()} for ${brandName}.`,
            "I am composing each asset's prompt without generating yet.",
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
          { brand: brandSlug, pack, dry_run: true },
          ac.signal,
        );
        const plan = readPlannedItems(run, brandSlug, pack);
        if (!plan) {
          const message = packErrorMessage(run);
          setCanvas({ status: "error", message });
          append(say("note", message, { error: true }));
          return;
        }

        setCanvas({ status: "pack_plan", plan });
        const n = plan.items.length;
        const failed = plan.items.filter((i) => i.status === "error").length;
        append(
          say(
            "pack",
            `Here is the ${packLabel.toLowerCase()}: ${n} ${n === 1 ? "asset" : "assets"}.` +
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
        append(say("note", message, { error: true }));
      }
    },
    [append, say, brands, controls.brand],
  );

  // ---- Research path: hand a brand to the research specialist ----
  const runResearch = useCallback(
    async (brandSlug: string | null, topic: string | null): Promise<void> => {
      if (!brandSlug) {
        append(
          say("note", "Tell me which brand to research and I will have research pull it.", {
            error: true,
          }),
        );
        return;
      }
      const brandName =
        brands.find((b) => b.slug === brandSlug)?.display_name ?? brandSlug;
      append(
        say("plan", `I am having research look into ${brandName}.`, {
          details: topic ? [`Focus: ${topic}.`] : undefined,
        }),
      );

      researchAbort.current?.abort();
      const ac = new AbortController();
      researchAbort.current = ac;
      try {
        const payload: Record<string, unknown> = { brand: brandSlug };
        if (topic) payload.topic = topic;
        const run = await api.runAgent("brand_research", payload, ac.signal);
        // The agents degrade to a non-ok status (200) when no gateway/Brigade is
        // configured; read the status rather than treating it as a throw.
        if (run.status === "ok" || run.status === "research_only") {
          append(
            say("note", `Research is back on ${brandName}. I will fold it into the work.`),
          );
        } else {
          append(
            say(
              "note",
              `My research specialist is not connected yet (it reported "${run.status}"). Connect Brigade to enable it.`,
            ),
          );
        }
      } catch (err) {
        if (ac.signal.aborted) return;
        const message =
          err instanceof Error ? err.message : "The research run failed.";
        append(say("note", message, { error: true }));
      }
    },
    [append, say, brands],
  );

  // ---- Single-asset path: drive the controls + prompt preview ----
  const runSingle = useCallback(
    async (
      text: string,
      hints?: { brand?: string | null; asset?: string | null; variant?: string | null; topic?: string | null },
    ): Promise<void> => {
      const plan = planFromRequest({
        text,
        brands,
        assetTypes,
        knownVariants: variantOptions,
        controls: {
          brand: hints?.brand ?? controls.brand,
          asset: hints?.asset ?? controls.asset,
          variant: hints?.variant ?? controls.variant,
          topic: hints?.topic ?? controls.topic,
          platform: null,
          backend: controls.backend,
        },
      });

      if (!plan) {
        append(
          say(
            "note",
            brands.length === 0 || assetTypes.length === 0
              ? "I am still loading your brands and asset types. Try again in a moment."
              : "I could not tell which asset you mean. Open Fine-tune to set it and resend.",
            { error: true },
          ),
        );
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

      // Compose and announce the prompt; open Fine-tune so review + Generate are
      // in reach. The controls effect would also fire, but we drive it here so
      // the conversation reflects this exact request.
      setFineTuneOpen(true);
      lastReqKey.current = JSON.stringify(plan.request);
      await runPreview(plan.request, { announce: true });
    },
    [append, say, brands, assetTypes, variantOptions, controls, runPreview],
  );

  // ---- Deterministic fallback: used only when the Director call fails ----
  const planLocally = useCallback(
    async (text: string): Promise<void> => {
      const packIntent = inferPack(text);
      if (packIntent) {
        const brand = resolvePackBrand(text, brands, controls.brand);
        await runPack(brand, packIntent.pack, packIntent.reason);
        return;
      }
      await runSingle(text);
    },
    [brands, controls.brand, runPack, runSingle],
  );

  // ---- Send: route free text through Iris, then act on her intent ----
  const directorAbort = useRef<AbortController | null>(null);
  const handleSend = useCallback(
    async (text: string) => {
      append(userTurn(text));
      setThinking(true);
      // Drop any prior created-agent card; this turn supersedes it.
      setAgentCard(null);

      const history = buildHistory();
      directorAbort.current?.abort();
      const ac = new AbortController();
      directorAbort.current = ac;

      let reply: DirectorReply;
      try {
        reply = await api.directorChat(
          { message: text, brand: activeSlug, history },
          ac.signal,
        );
      } catch {
        if (ac.signal.aborted) {
          setThinking(false);
          return;
        }
        // The Director endpoint degrades in persona on its own (200), so a throw
        // here is a transport failure. Fall back to the deterministic planner.
        await planLocally(text);
        setThinking(false);
        return;
      }

      // Render Iris's reply as her turn.
      append(say("plan", reply.reply));

      // Act on the resolved intent.
      const replyBrand = reply.brand ?? activeSlug;
      switch (reply.intent) {
        case "pack": {
          const pack = asPackName(reply.pack) ?? DEFAULT_PACK;
          const brand = replyBrand ?? resolvePackBrand(text, brands, controls.brand);
          await runPack(
            brand,
            pack,
            `I will plan the ${(PACK_LABELS[pack] ?? pack).toLowerCase()}.`,
          );
          break;
        }
        case "research": {
          const brand = replyBrand ?? resolvePackBrand(text, brands, controls.brand);
          await runResearch(brand, reply.topic);
          break;
        }
        case "single": {
          await runSingle(text, {
            brand: replyBrand,
            asset: reply.asset,
            variant: reply.variant,
            topic: reply.topic,
          });
          break;
        }
        case "agent": {
          // Iris created (or already had) an on-brand agent identity server-side
          // and returned it. Her narration already rendered above; surface the
          // created-agent card so the operator can generate its avatar or open
          // it on the brand. The route only returns `agent` when it resolved a
          // brand + name, so a null here degrades to her reply alone.
          if (reply.agent) setAgentCard(reply.agent);
          break;
        }
        case "chat":
        default: {
          // Backstop: Iris sometimes labels a clear kit request as chat. Catch
          // the high-confidence pack case deterministically so the work still
          // fires (kits are the core job). A plain question falls through to her
          // reply; single assets stay available via Fine-tune.
          const packIntent = inferPack(text);
          if (packIntent) {
            const brand = replyBrand ?? resolvePackBrand(text, brands, controls.brand);
            await runPack(brand, packIntent.pack, packIntent.reason);
          }
          break;
        }
      }

      setThinking(false);
    },
    [
      append,
      say,
      buildHistory,
      activeSlug,
      planLocally,
      brands,
      controls.brand,
      runPack,
      runResearch,
      runSingle,
    ],
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
      say("generating", count > 1 ? `Generating ${count} versions.` : "Generating."),
    );

    try {
      const result = await api.generate(req, ac.signal);
      setCanvas({ status: "results", result });
      if (result.files.length > 0) setSelectedIndex(0);
      append(
        say(
          "result",
          result.files.length > 0
            ? `Done. ${result.files.length} ${
                result.files.length === 1 ? "asset" : "assets"
              } in the canvas.`
            : "The run finished but returned no files.",
        ),
      );
    } catch (err) {
      if (ac.signal.aborted) return;
      if (isNeedsKeyError(err)) {
        const { backend, detail } = err.needsKey;
        setCanvas({ status: "needs_key", backend, detail });
        append(
          say("note", `The ${backend} backend needs a key before I can generate.`),
        );
      } else {
        const message = err instanceof Error ? err.message : "Generation failed.";
        setCanvas({ status: "error", message });
        append(say("note", message, { error: true }));
      }
    } finally {
      if (!ac.signal.aborted) setGenerating(false);
    }
  }, [controls, count, append, say]);

  // Abort any in-flight requests on unmount.
  useEffect(() => {
    return () => {
      previewAbort.current?.abort();
      generateAbort.current?.abort();
      packAbort.current?.abort();
      researchAbort.current?.abort();
      directorAbort.current?.abort();
    };
  }, []);

  const busy = thinking || (previewLoading && !preview);

  // The agent's opening line and header context line scope to whoever is in the
  // room: Iris cross-brand, or the active brand's steward.
  const brandCount = brands.length;
  const opener = useMemo(() => {
    if (activeBrand) {
      return `I look after ${activeBrand.display_name}. I have its palette, type, and rules loaded. What do you need?`;
    }
    return "I am Iris, your brand director. I look after your brands and make the assets you need, on brand. What are we working on?";
  }, [activeBrand]);

  const headerContext = useMemo(() => {
    if (activeBrand) return `Looking after ${activeBrand.display_name}`;
    if (brandCount === 0) return "Looking after your brands";
    return `Knows your ${brandCount} ${brandCount === 1 ? "brand" : "brands"}`;
  }, [activeBrand, brandCount]);

  const suggestions = useMemo(() => {
    if (activeBrand) {
      const name = activeBrand.display_name;
      return [
        `Build the launch kit for ${name}`,
        `Make an OG image for ${name}, dark`,
        `Check ${name}'s latest assets against the brand rules`,
      ];
    }
    const sample = brands[0]?.display_name ?? "a brand";
    return [
      `Build the launch kit for ${sample}`,
      "Make an OG image for the investor update, dark",
      `Research ${sample} and refresh what you know`,
    ];
  }, [activeBrand, brands]);

  const onControlsChange = useCallback((next: ControlsState) => {
    brandTouched.current = true;
    setControls(next);
  }, []);

  return (
    <div className="space-y-5">
      <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-text-faint">
        <Sparkles size={13} className="text-accent" /> Studio
      </p>

      {/* Who you are working with: Iris, or the focused brand's steward. */}
      <StudioHeader agent={agent} context={headerContext} />

      {/* A new-brand handoff puts Iris's intake card on top: she drafts a full
          identity and creates the brand. Dismissing it returns to the normal
          conversation below. */}
      {newBrandMode && (
        <IntakePanel
          mode={newBrandMode}
          brands={brands}
          activeSlug={activeSlug}
          onClose={() => setNewBrandMode(null)}
        />
      )}

      {/* The hero: a large conversation beside the results canvas. */}
      <div className="grid h-[calc(100vh-19rem)] min-h-[26rem] grid-cols-1 gap-4 lg:grid-cols-[1.15fr_1fr]">
        <ConversationPanel
          agent={agent}
          opener={opener}
          turns={turns}
          busy={busy}
          onSend={handleSend}
          suggestions={suggestions}
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

      {/* The agent Iris just created from the conversation (intent "agent"): a
          compact card to generate its avatar or open it on its brand. Sits with
          the conversation that produced it. */}
      {agentCard && (
        <CreatedAgentCard
          key={`${agentCard.brand}/${agentCard.slug}`}
          agent={agentCard}
          onClose={() => setAgentCard(null)}
        />
      )}

      {/* Manual controls, demoted behind a toggle. Closed by default. */}
      <FineTunePanel
        open={fineTuneOpen}
        onToggle={() => setFineTuneOpen((v) => !v)}
      >
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
      </FineTunePanel>
    </div>
  );
}
