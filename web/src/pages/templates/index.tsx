/**
 * Template Studio: locked, on-brand templates you fill with text and export.
 *
 * Left: pick a template (the locked layouts), pick the brand (defaults to the
 * active brand), and fill the text slots. Right: a live canvas preview that
 * re-renders on every change from the brand's tokens and mark, plus Export PNG
 * and Save to library.
 *
 * The layout, fonts, colors, and mark are fixed by the TemplateDef; editing is
 * limited to text slot CONTENT, so every export is on-brand by construction. The
 * preview is drawn deterministically by renderTemplate (no AI image model, no
 * network in the render). Save POSTs the rendered PNG to the existing
 * /api/assets/upload so it lands in the brand Library and flows through the
 * compliance gate.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  LayoutTemplate,
  AlertTriangle,
  Download,
  Save,
  CheckCircle2,
  ImageOff,
} from "lucide-react";
import { GlassPanel } from "../../components/GlassPanel";
import { SpotlightCard } from "../../components/SpotlightCard";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { api, ApiError } from "../../api/client";
import type { BrandTokens, TemplateDef, TemplateSlot } from "../../lib/types";
import { useActiveBrand } from "../../brand-context";
import { spring, staggerContainer, staggerItem } from "../../lib/motion";
import { renderTemplate } from "./render";

// ---------------------------------------------------------------------------
// Small hooks
// ---------------------------------------------------------------------------

/** True once document.fonts.ready resolves, so the first render uses real fonts. */
function useFontsReady(): boolean {
  const [ready, setReady] = useState(
    () => typeof document !== "undefined" && (document as Document).fonts?.status === "loaded",
  );
  useEffect(() => {
    if (typeof document === "undefined" || !document.fonts) {
      setReady(true);
      return;
    }
    let alive = true;
    document.fonts.ready.then(() => {
      if (alive) setReady(true);
    });
    return () => {
      alive = false;
    };
  }, []);
  return ready;
}

/**
 * Load an image URL into an HTMLImageElement for use as a canvas source. Returns
 * the element only once it has decoded (so the canvas draws it at its natural
 * size), plus a loading/error flag. crossOrigin is set so toBlob never trips the
 * tainted-canvas guard; the mark is served same-origin from /api/assets/file, so
 * this is defensive. A null URL resolves to no image (templates without a mark).
 */
function useMarkImage(url: string | null | undefined): {
  image: HTMLImageElement | null;
  loading: boolean;
  failed: boolean;
} {
  const [state, setState] = useState<{
    image: HTMLImageElement | null;
    loading: boolean;
    failed: boolean;
  }>({ image: null, loading: Boolean(url), failed: false });

  useEffect(() => {
    if (!url) {
      setState({ image: null, loading: false, failed: false });
      return;
    }
    let alive = true;
    setState({ image: null, loading: true, failed: false });
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      if (alive) setState({ image: img, loading: false, failed: false });
    };
    img.onerror = () => {
      if (alive) setState({ image: null, loading: false, failed: true });
    };
    img.src = url;
    return () => {
      alive = false;
      img.onload = null;
      img.onerror = null;
    };
  }, [url]);

  return state;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** A slot's current value, falling back to its default text. */
function valueOf(slot: TemplateSlot, values: Record<string, string>): string {
  return values[slot.key] ?? slot.default ?? "";
}

/** Seed the editor's values from a template's text slot defaults. */
function defaultsFor(def: TemplateDef): Record<string, string> {
  const out: Record<string, string> = {};
  for (const slot of def.slots) {
    if (slot.kind === "text") out[slot.key] = slot.default ?? "";
  }
  return out;
}

/** A short, file-safe download name like "magic-unicorn-og-card.png". */
function downloadName(brandSlug: string, def: TemplateDef): string {
  const safe = (s: string) => s.replace(/[^a-z0-9._-]+/gi, "-").toLowerCase();
  return `${safe(brandSlug)}-${safe(def.id)}.png`;
}

/** Promisified canvas.toBlob (PNG). Rejects when the canvas yields no blob. */
function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The canvas could not be exported."));
    }, "image/png");
  });
}

/** A "16:9"-style ratio string for the preview frame's aspect-ratio. */
function ratio(def: TemplateDef): string {
  return `${def.width} / ${def.height}`;
}

// ---------------------------------------------------------------------------
// Subcomponents
// ---------------------------------------------------------------------------

/** A single template option in the left-hand picker. */
function TemplatePickerCard({
  def,
  selected,
  onSelect,
}: {
  def: TemplateDef;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <motion.button
      type="button"
      variants={staggerItem}
      onClick={onSelect}
      aria-pressed={selected}
      className={[
        "group relative flex w-full flex-col gap-2 rounded-xl border p-3 text-left transition-colors",
        selected
          ? "border-accent/60 bg-accent/10 shadow-glow"
          : "border-white/10 bg-white/[0.03] hover:border-accent/40 hover:bg-white/[0.06]",
      ].join(" ")}
    >
      {/* A tiny proportion chip so the card reads its shape at a glance. */}
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className={[
            "block shrink-0 rounded-[3px] ring-1",
            selected ? "bg-accent/30 ring-accent/50" : "bg-white/10 ring-white/15",
          ].join(" ")}
          style={{
            width: 34,
            height: Math.max(10, Math.round((def.height / def.width) * 34)),
          }}
        />
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-text">{def.label}</p>
          <p className="text-xs tabular-nums text-text-faint">
            {def.width} × {def.height}
          </p>
        </div>
      </div>
    </motion.button>
  );
}

/** The brand picker (a labeled native select; defaults to the active brand). */
function BrandPicker({
  value,
  options,
  onChange,
}: {
  value: string | null;
  options: { slug: string; label: string }[];
  onChange: (slug: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-text-muted">
        Brand
      </span>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 w-full rounded-lg border border-chrome-border bg-chrome-raised px-3 text-sm text-text outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent/60"
      >
        {options.length === 0 && <option value="">No brands</option>}
        {options.map((o) => (
          <option key={o.slug} value={o.slug}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** One editable text slot field: an input, or a textarea for long copy. */
function SlotField({
  slot,
  value,
  onChange,
}: {
  slot: TemplateSlot;
  value: string;
  onChange: (next: string) => void;
}) {
  // Long-form slots (quotes, body copy) get a textarea; short ones an input.
  // Heuristic: a generous max_len, or a "body" font role, reads as multiline.
  const multiline =
    (slot.max_len ?? 0) > 80 || slot.font === "body";
  const count = value.length;
  const max = slot.max_len ?? undefined;
  const near = max !== undefined && count > max * 0.9;

  const shared =
    "w-full rounded-lg border border-chrome-border bg-chrome-raised px-3 py-2 text-sm text-text outline-none transition-colors placeholder:text-text-faint focus-visible:ring-2 focus-visible:ring-accent/60";

  return (
    <label className="block">
      <span className="mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-text-muted">{slot.label}</span>
        {max !== undefined && (
          <span
            className={[
              "text-[11px] tabular-nums",
              near ? "text-amber-300" : "text-text-faint",
            ].join(" ")}
          >
            {count}/{max}
          </span>
        )}
      </span>
      {multiline ? (
        <textarea
          value={value}
          maxLength={max}
          rows={3}
          onChange={(e) => onChange(e.target.value)}
          placeholder={slot.default ?? ""}
          className={`${shared} resize-y`}
        />
      ) : (
        <input
          type="text"
          value={value}
          maxLength={max}
          onChange={(e) => onChange(e.target.value)}
          placeholder={slot.default ?? ""}
          className={shared}
        />
      )}
    </label>
  );
}

// ---------------------------------------------------------------------------
// The preview canvas (memo-free; re-renders imperatively on dep changes)
// ---------------------------------------------------------------------------

interface PreviewProps {
  def: TemplateDef;
  tokens: BrandTokens | undefined;
  markImage: HTMLImageElement | null;
  values: Record<string, string>;
  fontsReady: boolean;
  /** Receives the live canvas element so the parent can export/save it. */
  canvasRef: React.MutableRefObject<HTMLCanvasElement | null>;
}

function Preview({
  def,
  tokens,
  markImage,
  values,
  fontsReady,
  canvasRef,
}: PreviewProps) {
  const localRef = useRef<HTMLCanvasElement | null>(null);

  // Re-render the canvas whenever any input changes. Skip until fonts are ready
  // and tokens are loaded so the first paint already uses the correct fonts and
  // brand colors (no flash of a wrong typeface).
  useEffect(() => {
    const canvas = localRef.current;
    if (!canvas) return;
    canvasRef.current = canvas;
    if (!tokens || !fontsReady) return;
    renderTemplate(canvas, def, tokens, markImage, values);
  }, [def, tokens, markImage, values, fontsReady, canvasRef]);

  return (
    <div
      // A subtle checkerboard so a light/transparent render still reads as art
      // on a board, with the brand dot-grid layered faintly over it.
      className="relative mx-auto w-full max-w-2xl overflow-hidden rounded-xl border border-white/10 shadow-float"
      style={{
        aspectRatio: ratio(def),
        backgroundColor: "#15151f",
        backgroundImage:
          "linear-gradient(45deg, rgba(255,255,255,0.04) 25%, transparent 25%, transparent 75%, rgba(255,255,255,0.04) 75%, rgba(255,255,255,0.04))," +
          "linear-gradient(45deg, rgba(255,255,255,0.04) 25%, transparent 25%, transparent 75%, rgba(255,255,255,0.04) 75%, rgba(255,255,255,0.04))",
        backgroundSize: "24px 24px",
        backgroundPosition: "0 0, 12px 12px",
      }}
    >
      <motion.canvas
        key={def.id}
        ref={localRef}
        initial={{ opacity: 0, scale: 0.985 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={spring.card}
        // The canvas backing store is def.width x def.height; CSS scales it to
        // fit the frame. The frame's aspect-ratio matches, so there is no skew.
        className="block h-full w-full"
      />
      {/* While fonts/tokens load, a calm overlay rather than a wrong-font flash. */}
      {(!tokens || !fontsReady) && (
        <div className="absolute inset-0 grid place-items-center bg-chrome-bg/40 backdrop-blur-sm">
          <span className="flex items-center gap-2 text-sm text-text-muted">
            <Spinner size={16} /> Preparing preview
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; file: string }
  | { kind: "error"; message: string };

export default function TemplatesPage() {
  const { brands, activeSlug } = useActiveBrand();
  const queryClient = useQueryClient();
  const fontsReady = useFontsReady();

  // The live canvas, lifted here so Export/Save can read it.
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Templates (the locked layouts).
  const {
    data: templates,
    isLoading: templatesLoading,
    isError: templatesError,
    error: templatesErr,
    refetch: refetchTemplates,
  } = useQuery({
    queryKey: ["templates"],
    queryFn: ({ signal }) => api.listTemplates(signal),
  });

  // Which template is selected. Defaults to the first once loaded.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected: TemplateDef | null = useMemo(() => {
    if (!templates || templates.length === 0) return null;
    return templates.find((t) => t.id === selectedId) ?? templates[0];
  }, [templates, selectedId]);

  // The Studio's own brand selection, seeded from the app's active brand.
  const [brandSlug, setBrandSlug] = useState<string | null>(null);
  const [brandSeeded, setBrandSeeded] = useState(false);
  useEffect(() => {
    if (!brandSeeded && activeSlug) {
      setBrandSlug(activeSlug);
      setBrandSeeded(true);
    }
  }, [brandSeeded, activeSlug]);
  // Fall back to the first brand if the active one never resolves.
  useEffect(() => {
    if (!brandSlug && brands.length > 0) setBrandSlug(brands[0].slug);
  }, [brandSlug, brands]);

  const brandOptions = useMemo(
    () => brands.map((b) => ({ slug: b.slug, label: b.display_name })),
    [brands],
  );
  const brandSummary = useMemo(
    () => brands.find((b) => b.slug === brandSlug) ?? null,
    [brands, brandSlug],
  );

  // The brand's tokens drive every color and font in the render.
  const {
    data: tokens,
    isLoading: tokensLoading,
    isError: tokensError,
    error: tokensErr,
    refetch: refetchTokens,
  } = useQuery({
    queryKey: ["brand-tokens", brandSlug],
    queryFn: ({ signal }) => api.getBrandTokens(brandSlug as string, signal),
    enabled: Boolean(brandSlug),
  });

  // The brand mark image (the logo) for any mark slot.
  const mark = useMarkImage(brandSummary?.mark_url);
  const templateUsesMark = Boolean(
    selected?.slots.some((s) => s.kind === "mark"),
  );

  // Editor values, keyed by slot. Reset to the template's defaults whenever the
  // selected template changes (each template has its own slots).
  const [values, setValues] = useState<Record<string, string>>({});
  useEffect(() => {
    if (selected) setValues(defaultsFor(selected));
  }, [selected]);

  const setSlotValue = useCallback((key: string, next: string) => {
    setValues((prev) => ({ ...prev, [key]: next }));
  }, []);

  // Save feedback (inline confirmation + error). Cleared on any edit.
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  useEffect(() => {
    setSaveState({ kind: "idle" });
  }, [selected, brandSlug]);

  // Export: render the current canvas to a PNG and trigger a download.
  const [exporting, setExporting] = useState(false);
  const handleExport = useCallback(async () => {
    const canvas = canvasRef.current;
    if (!canvas || !selected || !brandSlug) return;
    setExporting(true);
    try {
      const blob = await canvasToPngBlob(canvas);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = downloadName(brandSlug, selected);
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Release the object URL on the next tick so the download has started.
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setSaveState({
        kind: "error",
        message: "Could not export the PNG. Try again.",
      });
    } finally {
      setExporting(false);
    }
  }, [selected, brandSlug]);

  // Save: POST the rendered PNG to the existing upload endpoint, tagged with the
  // template's asset type and id (as the variant), so it lands in the Library
  // and runs through the compliance gate. On success, invalidate the assets
  // queries so the Library shows it without a reload.
  const saveMutation = useMutation({
    mutationFn: async () => {
      const canvas = canvasRef.current;
      if (!canvas || !selected || !brandSlug) {
        throw new Error("Nothing to save yet.");
      }
      const blob = await canvasToPngBlob(canvas);
      const file = new File([blob], downloadName(brandSlug, selected), {
        type: "image/png",
      });
      return api.upload(file, brandSlug, selected.asset_type, selected.id);
    },
    onMutate: () => setSaveState({ kind: "saving" }),
    onSuccess: (record) => {
      setSaveState({ kind: "saved", file: record.file });
      void queryClient.invalidateQueries({ queryKey: ["assets"] });
      void queryClient.invalidateQueries({ queryKey: ["activity"] });
    },
    onError: (err) => {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Save failed.";
      setSaveState({ kind: "error", message });
    },
  });

  const brandLabel = brandSummary?.display_name ?? brandSlug ?? "the brand";
  const canAct = Boolean(selected && brandSlug && tokens && fontsReady);

  // -- Render states -------------------------------------------------------

  if (templatesLoading) {
    return (
      <GlassPanel title="Template Studio">
        <div className="flex items-center justify-center gap-3 py-20 text-sm text-text-muted">
          <Spinner size={18} /> Loading templates
        </div>
      </GlassPanel>
    );
  }

  if (templatesError) {
    return (
      <GlassPanel title="Template Studio">
        <EmptyState
          icon={<AlertTriangle size={26} />}
          title="Could not load templates"
          description={
            templatesErr instanceof Error
              ? templatesErr.message
              : "The templates request failed."
          }
          action={
            <Button variant="secondary" onClick={() => refetchTemplates()}>
              Try again
            </Button>
          }
        />
      </GlassPanel>
    );
  }

  if (!templates || templates.length === 0 || !selected) {
    return (
      <GlassPanel title="Template Studio">
        <EmptyState
          icon={<LayoutTemplate size={28} />}
          title="No templates available"
          description="Locked, on-brand templates appear here. None are configured yet."
        />
      </GlassPanel>
    );
  }

  const textSlots = selected.slots.filter((s) => s.kind === "text");

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-display text-2xl font-semibold tracking-tight text-text">
          Template Studio
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-text-muted">
          Fill a locked template with your words. The layout, fonts, colors, and
          mark are set by the brand, so the result is on-brand by construction.
          Export a PNG or save it to the brand Library.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)]">
        {/* ---- Left: picker + brand + form ---- */}
        <div className="space-y-4">
          <GlassPanel title="Template">
            <motion.div
              variants={staggerContainer}
              initial="hidden"
              animate="show"
              className="grid grid-cols-1 gap-2"
            >
              {templates.map((t) => (
                <TemplatePickerCard
                  key={t.id}
                  def={t}
                  selected={t.id === selected.id}
                  onSelect={() => setSelectedId(t.id)}
                />
              ))}
            </motion.div>
          </GlassPanel>

          <GlassPanel title="Content">
            <div className="space-y-4">
              <BrandPicker
                value={brandSlug}
                options={brandOptions}
                onChange={setBrandSlug}
              />

              {/* A note when the chosen template uses the mark but the brand has
                  none, or the mark failed to load: the render still works, the
                  mark area is just empty. */}
              {templateUsesMark && !mark.loading && !brandSummary?.mark_url && (
                <p className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-text-muted">
                  <ImageOff size={14} className="mt-0.5 shrink-0" />
                  {brandLabel} has no mark set, so the logo area stays empty.
                </p>
              )}
              {templateUsesMark && mark.failed && (
                <p className="flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-300">
                  <ImageOff size={14} className="mt-0.5 shrink-0" />
                  The brand mark could not be loaded; the logo area stays empty.
                </p>
              )}

              <div className="space-y-3">
                {textSlots.map((slot) => (
                  <SlotField
                    key={slot.key}
                    slot={slot}
                    value={valueOf(slot, values)}
                    onChange={(next) => setSlotValue(slot.key, next)}
                  />
                ))}
                {textSlots.length === 0 && (
                  <p className="text-sm text-text-muted">
                    This template has no editable text.
                  </p>
                )}
              </div>
            </div>
          </GlassPanel>
        </div>

        {/* ---- Right: live preview + actions ---- */}
        <GlassPanel
          title="Preview"
          ring
          actions={
            <span className="text-xs tabular-nums text-text-faint">
              {selected.width} × {selected.height}
            </span>
          }
        >
          <div className="space-y-4">
            <SpotlightCard className="rounded-2xl">
              <div className="p-1.5">
                <Preview
                  def={selected}
                  tokens={tokens}
                  markImage={mark.image}
                  values={values}
                  fontsReady={fontsReady}
                  canvasRef={canvasRef}
                />
              </div>
            </SpotlightCard>

            {/* Token / brand load error: the preview cannot draw without tokens. */}
            {tokensError && (
              <p className="flex items-center justify-between gap-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                <span className="flex items-center gap-2">
                  <AlertTriangle size={14} />
                  {tokensErr instanceof Error
                    ? tokensErr.message
                    : "Could not load the brand tokens."}
                </span>
                <button
                  type="button"
                  onClick={() => refetchTokens()}
                  className="font-medium underline-offset-2 hover:underline"
                >
                  Retry
                </button>
              </p>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="primary"
                icon={<Download size={16} />}
                onClick={() => void handleExport()}
                loading={exporting}
                disabled={!canAct}
              >
                Export PNG
              </Button>
              <Button
                variant="secondary"
                icon={<Save size={16} />}
                onClick={() => saveMutation.mutate()}
                loading={saveState.kind === "saving"}
                disabled={!canAct || saveState.kind === "saving"}
              >
                Save to {brandLabel} library
              </Button>

              {/* Inline save confirmation / error, beside the actions. */}
              {saveState.kind === "saved" && (
                <motion.span
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex items-center gap-1.5 text-sm text-accent"
                >
                  <CheckCircle2 size={16} /> Saved to the Library
                </motion.span>
              )}
              {saveState.kind === "error" && (
                <motion.span
                  initial={{ opacity: 0, x: -4 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="flex items-center gap-1.5 text-sm text-red-300"
                >
                  <AlertTriangle size={16} /> {saveState.message}
                </motion.span>
              )}
              {(tokensLoading || (mark.loading && templateUsesMark)) &&
                !tokensError && (
                  <span className="flex items-center gap-2 text-xs text-text-muted">
                    <Spinner size={13} /> Loading brand
                  </span>
                )}
            </div>
          </div>
        </GlassPanel>
      </div>
    </div>
  );
}
