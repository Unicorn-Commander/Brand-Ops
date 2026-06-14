import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowRight, Globe, Sparkles, X } from "lucide-react";
import { api, ApiError } from "../../../api/client";
import { GlassPanel } from "../../../components/GlassPanel";
import { Button } from "../../../components/Button";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { Director } from "../../../lib/agents";
import { spring, staggerContainer, staggerItem } from "../../../lib/motion";
import { useViewMode } from "../../../view-mode";
import type {
  BrandSummary,
  IntakeDraft,
  IntakeMode,
  IntakeResult,
} from "../../../lib/types";

/** The new-brand modes the Brands page hands the Studio. */
export type NewBrandMode = "consistent" | "distinct" | "onboard";

/** True when a string is one of the three new-brand modes. */
export function isNewBrandMode(value: string | null): value is NewBrandMode {
  return value === "consistent" || value === "distinct" || value === "onboard";
}

interface IntakePanelProps {
  /** Which way the operator chose to start the brand. */
  mode: NewBrandMode;
  /** The brands already loaded, for the parent picker. */
  brands: BrandSummary[];
  /** The app's active brand slug, used to prefill the parent in "consistent". */
  activeSlug: string | null;
  /** Dismiss the panel and fall back to the normal conversation. */
  onClose: () => void;
}

type ModeCopy = { role: string; lead: string; hint: string };

/**
 * Per-mode framing copy, spoken by Iris in first person, in two voices keyed by
 * the app's Simple/Advanced view mode (same audience-as-locale idea as
 * `NEW_BRAND_MODES`). Simple is plain goal-language for a newcomer; Advanced
 * keeps the house voice and surfaces the technical detail (inheritance, live
 * capture). Both honor the copy rule: no em-dashes, no marketing-speak.
 */
const MODE_COPY_ADVANCED: Record<NewBrandMode, ModeCopy> = {
  consistent: {
    role: "drafting a sub-brand",
    lead: "Tell me the name and a line about what it is. I will draft an identity that stays consistent with its parent, then you refine it.",
    hint: "A sub-brand inherits the parent's palette, type, and voice.",
  },
  distinct: {
    role: "drafting a new brand",
    lead: "Give me the name and a line about the brand. I will draft a distinct identity (palette, type, voice) for you to refine.",
    hint: "A clean slate with its own identity.",
  },
  onboard: {
    role: "onboarding from a website",
    lead: "Give me the name and the site URL. I will draft a brand profile from them.",
    hint: "Deep live-site capture needs Brigade connected; until then I draft from the URL and the name.",
  },
};

const MODE_COPY_SIMPLE: Record<NewBrandMode, ModeCopy> = {
  consistent: {
    role: "adding a sub-brand",
    lead: "Tell me the name and a sentence about it. I will create a related brand under one you already have, then you can change anything.",
    hint: "It reuses the colors, type, and voice of the brand you pick.",
  },
  distinct: {
    role: "starting from scratch",
    lead: "Tell me the name and a sentence about it. I will draft the colors, type, and voice for you to change.",
    hint: "A fresh start with its own look.",
  },
  onboard: {
    role: "importing from a website",
    lead: "Give me the name and your website address. I will pull a starting look from your site.",
    hint: "Full site reading is coming. For now I work from the address and the name.",
  },
};

/**
 * Map the UI mode to the API's intake mode. "onboard" sources from a URL; the
 * other two describe from the name + a line (a sub-brand is signalled by its
 * parent, not by a distinct mode).
 */
function toApiMode(mode: NewBrandMode): IntakeMode {
  return mode === "onboard" ? "onboard" : "describe";
}

/**
 * Derive a slug from a display name the same way the server does: lowercase,
 * non-alphanumerics collapsed to single hyphens, edges trimmed. Used only to
 * preview the URL the brand will live at; the server is the source of truth.
 */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** A checkerboard so light or near-bg swatches stay visible on dark chrome. */
const CHECKER =
  "repeating-conic-gradient(#2a2a3d 0% 25%, #1b1b27 0% 50%) 50% / 12px 12px";

/** The five palette roles in the order the brand sheet shows them. */
const PALETTE_ROLES: { key: keyof IntakeDraft["palette"]; label: string }[] = [
  { key: "primary", label: "Primary" },
  { key: "accent", label: "Accent" },
  { key: "background", label: "Background" },
  { key: "foreground", label: "Foreground" },
  { key: "muted", label: "Muted" },
];

/**
 * One drafted color: the swatch over a checker, its role, and the hex. A role
 * the draft leaves unset renders a clear "not set" placeholder, like the brand
 * sheet's palette.
 */
function DraftSwatch({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div
        className="relative flex h-12 w-full items-center justify-center overflow-hidden rounded-lg ring-1 ring-white/10"
        style={{ background: CHECKER }}
      >
        {value ? (
          <span className="absolute inset-0" style={{ backgroundColor: value }} />
        ) : (
          <span className="text-[10px] text-text-faint">not set</span>
        )}
      </div>
      <div className="space-y-0.5">
        <p className="text-[11px] font-medium text-text">{label}</p>
        <p className="font-mono text-[11px] uppercase text-text-muted tabular-nums">
          {value ?? "—"}
        </p>
      </div>
    </div>
  );
}

/** A small label-less chip used for voice tone and style keywords. */
function Chip({ children }: { children: string }) {
  return (
    <span className="rounded-full border border-white/8 bg-white/[0.02] px-2.5 py-0.5 text-[11px] text-text-muted">
      {children}
    </span>
  );
}

/**
 * The drafted identity rendered as Iris speaking: her avatar + name, the
 * palette as swatches, the type and voice-tone chips, the tagline, her one-line
 * rationale, and any honest notes. The "Open <Brand>" button is owned by the
 * panel (it needs the close handler + navigation), so this stays presentational.
 */
function DraftReveal({ result }: { result: IntakeResult }) {
  const { brand, draft, notes } = result;
  const fonts = [
    draft.typography.display_font,
    draft.typography.primary_font,
    draft.typography.mono_font,
  ].filter(Boolean);
  // De-duplicate the font stack (display + body are often the same family).
  const fontStack = Array.from(new Set(fonts));

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="space-y-4"
    >
      {/* Iris, speaking the draft. */}
      <motion.div variants={staggerItem} className="flex items-start gap-2.5">
        <AgentAvatar name={Director.name} src={null} size={30} ring={false} />
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex items-center gap-1.5 px-0.5">
            <span className="text-xs font-semibold tracking-tight text-text">
              {Director.name}
            </span>
            <span className="inline-flex items-center gap-1 text-[0.7rem] text-text-faint">
              <Sparkles size={12} /> draft
            </span>
          </div>
          <div className="glass space-y-3 rounded-2xl rounded-tl-sm px-3.5 py-3 text-sm text-text">
            <p className="leading-relaxed">
              Here is a starting identity for{" "}
              <span className="font-medium">{brand.display_name}</span>. Refine
              anything that is not right.
            </p>

            {/* Palette */}
            <div className="grid grid-cols-3 gap-2.5 sm:grid-cols-5">
              {PALETTE_ROLES.map((r) => (
                <DraftSwatch
                  key={r.key}
                  label={r.label}
                  value={draft.palette[r.key]}
                />
              ))}
            </div>

            {/* Type + voice */}
            {fontStack.length > 0 && (
              <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-text-faint">
                  Type
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {fontStack.map((f) => (
                    <Chip key={f}>{f}</Chip>
                  ))}
                </div>
              </div>
            )}
            {draft.voice_tone.length > 0 && (
              <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-text-faint">
                  Voice
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {draft.voice_tone.map((t) => (
                    <Chip key={t}>{t}</Chip>
                  ))}
                </div>
              </div>
            )}
            {draft.style_keywords.length > 0 && (
              <div className="space-y-1">
                <p className="text-[11px] uppercase tracking-wide text-text-faint">
                  Style
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {draft.style_keywords.map((k) => (
                    <Chip key={k}>{k}</Chip>
                  ))}
                </div>
              </div>
            )}

            {/* Tagline */}
            {draft.tagline && (
              <p className="border-l-2 border-accent/40 pl-3 text-sm italic text-text-muted">
                {draft.tagline}
              </p>
            )}

            {/* Rationale */}
            {draft.rationale && (
              <p className="leading-relaxed text-text-muted">
                {draft.rationale}
              </p>
            )}

            {/* Honest notes */}
            {notes.length > 0 && (
              <ul className="space-y-1 border-t border-white/8 pt-2 text-xs text-text-muted">
                {notes.map((n, i) => (
                  <li key={i} className="flex gap-1.5">
                    <span className="text-text-faint">-</span>
                    <span>{n}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}

/**
 * A focused intake card shown in the Studio when a new-brand mode is active.
 *
 * This is where the three "New brand" modes stop narrating and actually create a
 * brand. The operator gives a name (required), an optional one-line description,
 * a website URL (onboard mode), and a parent (consistent mode, prefilled to the
 * active brand). "Draft and create" calls POST /api/brands/intake: Iris drafts a
 * full identity, writes a real brand.yaml, and returns the created brand. While
 * it runs she shows a working state; on success the draft is revealed inline as
 * her speaking, with a primary "Open <Brand>" that navigates to the new brand
 * (and invalidates the brands cache so it appears everywhere). A 409 (slug
 * taken) or 422 (bad name) surfaces inline.
 */
export function IntakePanel({
  mode,
  brands,
  activeSlug,
  onClose,
}: IntakePanelProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const nameInputRef = useRef<HTMLInputElement>(null);
  const { isSimple } = useViewMode();

  // Plain voice in Simple, house voice in Advanced.
  const copy = (isSimple ? MODE_COPY_SIMPLE : MODE_COPY_ADVANCED)[mode];
  const isOnboard = mode === "onboard";
  const isConsistent = mode === "consistent";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  // The parent slug, only collected in "consistent" mode. Prefilled to the
  // active brand when it exists, else the first brand, else empty.
  const [parent, setParent] = useState<string>(() => {
    if (!isConsistent) return "";
    if (activeSlug && brands.some((b) => b.slug === activeSlug)) return activeSlug;
    return brands[0]?.slug ?? "";
  });

  // Keep the parent prefill in step until brands finish loading (the picker
  // starts empty when the list is, then settles on the active/first brand).
  useEffect(() => {
    if (!isConsistent || parent) return;
    if (activeSlug && brands.some((b) => b.slug === activeSlug)) {
      setParent(activeSlug);
    } else if (brands.length > 0) {
      setParent(brands[0].slug);
    }
  }, [isConsistent, parent, activeSlug, brands]);

  // Focus the name field on open.
  useEffect(() => {
    const t = window.setTimeout(() => nameInputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, []);

  const parentName = useMemo(
    () => brands.find((b) => b.slug === parent)?.display_name ?? parent,
    [brands, parent],
  );

  const intake = useMutation<IntakeResult, unknown, void>({
    mutationFn: () =>
      api.intakeBrand({
        name: name.trim(),
        description: description.trim() || null,
        url: isOnboard ? url.trim() || null : null,
        parent: isConsistent ? parent || null : null,
        mode: toApiMode(mode),
      }),
    onSuccess: () => {
      // The new brand exists now; refresh the lists that show it.
      void queryClient.invalidateQueries({ queryKey: ["brands"] });
    },
  });

  const result = intake.data ?? null;
  const submitting = intake.isPending;

  // A human-readable error line for the inline banner. The 409 and 422 carry
  // their own server message; anything else is a transport failure.
  const errorMessage = useMemo(() => {
    const err = intake.error;
    if (!err) return null;
    if (err instanceof ApiError) {
      if (err.status === 409) {
        return err.message || "A brand with that name already exists. Try another.";
      }
      if (err.status === 422) {
        return err.message || "That name will not work. Try a different one.";
      }
      return err.message || "I could not create the brand. Try again.";
    }
    return "I could not create the brand. Try again.";
  }, [intake.error]);

  const canSubmit = name.trim().length > 0 && !submitting && !result;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    intake.mutate();
  };

  const openBrand = () => {
    if (!result) return;
    navigate(`/brands/${encodeURIComponent(result.brand.slug)}`);
  };

  const slugPreview = slugify(name);

  return (
    <GlassPanel ring glow className="overflow-hidden">
      <div className="flex items-start justify-between gap-3 border-b border-white/8 px-5 py-4">
        <div className="flex items-start gap-3">
          <AgentAvatar name={Director.name} src={null} size={40} />
          <div className="min-w-0">
            <p className="font-display text-base font-semibold tracking-tight text-text">
              {Director.name}
              <span className="ml-2 align-middle text-xs font-normal text-text-faint">
                {copy.role}
              </span>
            </p>
            <p className="mt-0.5 max-w-md text-sm leading-relaxed text-text-muted">
              {copy.lead}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Cancel"
          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-text-faint transition-colors hover:bg-chrome-hover hover:text-text"
        >
          <X size={15} />
        </button>
      </div>

      <div className="space-y-4 p-5">
        {result ? (
          <>
            <DraftReveal result={result} />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                onClick={openBrand}
                icon={<ArrowRight size={15} />}
              >
                Open {result.brand.display_name}
              </Button>
              <Button variant="ghost" onClick={onClose}>
                Done
              </Button>
            </div>
          </>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            {/* Brand name (required) */}
            <div className="space-y-1.5">
              <label
                htmlFor="intake-name"
                className="block text-xs font-medium text-text-muted"
              >
                Brand name
              </label>
              <input
                id="intake-name"
                ref={nameInputRef}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Aurora Labs"
                disabled={submitting}
                className="w-full rounded-lg border border-chrome-border bg-chrome-raised/80 px-3 py-2 text-sm text-text placeholder:text-text-faint focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
              />
              {slugPreview && (
                <p className="px-0.5 font-mono text-[11px] text-text-faint">
                  /brands/{slugPreview}
                </p>
              )}
            </div>

            {/* One-line description (optional) */}
            <div className="space-y-1.5">
              <label
                htmlFor="intake-description"
                className="block text-xs font-medium text-text-muted"
              >
                One line about it{" "}
                <span className="text-text-faint">(optional)</span>
              </label>
              <input
                id="intake-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What it is and who it is for"
                disabled={submitting}
                className="w-full rounded-lg border border-chrome-border bg-chrome-raised/80 px-3 py-2 text-sm text-text placeholder:text-text-faint focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
              />
            </div>

            {/* Website URL (onboard mode only) */}
            {isOnboard && (
              <div className="space-y-1.5">
                <label
                  htmlFor="intake-url"
                  className="block text-xs font-medium text-text-muted"
                >
                  Website URL
                </label>
                <div className="flex items-center gap-2 rounded-lg border border-chrome-border bg-chrome-raised/80 px-3 focus-within:border-accent/50 focus-within:ring-2 focus-within:ring-accent/40">
                  <Globe size={15} className="shrink-0 text-text-faint" />
                  <input
                    id="intake-url"
                    type="url"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://example.com"
                    disabled={submitting}
                    className="w-full bg-transparent py-2 text-sm text-text placeholder:text-text-faint focus:outline-none disabled:opacity-50"
                  />
                </div>
              </div>
            )}

            {/* Parent picker (consistent mode only) */}
            {isConsistent && (
              <div className="space-y-1.5">
                <label
                  htmlFor="intake-parent"
                  className="block text-xs font-medium text-text-muted"
                >
                  Parent brand
                </label>
                {brands.length > 0 ? (
                  <>
                    <select
                      id="intake-parent"
                      value={parent}
                      onChange={(e) => setParent(e.target.value)}
                      disabled={submitting}
                      className="w-full rounded-lg border border-chrome-border bg-chrome-raised/80 px-3 py-2 text-sm text-text focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
                    >
                      {brands.map((b) => (
                        <option key={b.slug} value={b.slug}>
                          {b.display_name}
                        </option>
                      ))}
                    </select>
                    {parent && (
                      <p className="px-0.5 text-[11px] text-text-faint">
                        {isSimple
                          ? `Reuses the look of ${parentName}.`
                          : `Inherits and stays consistent with ${parentName}.`}
                      </p>
                    )}
                  </>
                ) : (
                  <p className="text-[11px] text-text-faint">
                    No brands to inherit from yet. I will draft a distinct
                    identity instead.
                  </p>
                )}
              </div>
            )}

            {/* The per-mode hint. Consistent mode shows its parent note inline
                with the picker instead, so the hint is for the other two. */}
            {!isConsistent && (
              <p className="text-[11px] text-text-faint">{copy.hint}</p>
            )}

            {/* Inline error (409 / 422 / transport) */}
            {errorMessage && (
              <div className="rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                {errorMessage}
              </div>
            )}

            {/* Working state: Iris is drafting. */}
            {submitting && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={spring.card}
                className="flex items-start gap-2.5"
              >
                <AgentAvatar
                  name={Director.name}
                  src={null}
                  size={30}
                  ring={false}
                />
                <div className="min-w-0">
                  <div className="mb-1 flex items-center gap-1.5 px-0.5">
                    <span className="text-xs font-semibold tracking-tight text-text">
                      {Director.name}
                    </span>
                    <span className="inline-flex items-center gap-1 text-[0.7rem] text-text-faint">
                      <Sparkles size={12} className="animate-pulse" /> working
                    </span>
                  </div>
                  <div className="glass rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm text-text">
                    <p className="leading-relaxed">
                      {isOnboard
                        ? `Reading ${url.trim() || "the site"} and drafting an identity for ${name.trim() || "your brand"}.`
                        : isConsistent
                          ? `Drafting an identity for ${name.trim() || "your sub-brand"}, consistent with ${parentName || "its parent"}.`
                          : `Drafting an identity for ${name.trim() || "your brand"}.`}
                    </p>
                  </div>
                </div>
              </motion.div>
            )}

            <div className="flex items-center gap-2">
              <Button
                type="submit"
                variant="primary"
                loading={submitting}
                disabled={!canSubmit}
                icon={!submitting ? <Sparkles size={15} /> : undefined}
              >
                Draft and create
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={onClose}
                disabled={submitting}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}
      </div>
    </GlassPanel>
  );
}
