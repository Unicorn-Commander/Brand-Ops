/**
 * Brand record — the brand sheet for one slug. Pulls the full brand (colors,
 * typography, voice) and the brand's assets, then presents the canonical brand
 * sheet: logo lockup + coverage, a copyable palette, type specimens, a coverage
 * checklist whose gaps deep-link into the generator, live mockups (app icon on a
 * phone, favicon in a tab, OG link-preview, business card), and an asset strip.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowLeft, Check, Copy, Sparkles, ShieldCheck, Ban, Type, BookOpen, Activity, GitFork, FlaskConical, ChevronRight } from "lucide-react";
import { getBrand, listAssetTypes, listAssets, getBrandTokens, cloneBrand } from "../../api/client";
import { ApiError } from "../../api/client";
import { GlassPanel } from "../../components/GlassPanel";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { CoverageRing } from "../../components/CoverageRing";
import { AssetThumb } from "../../components/AssetThumb";
import { AgentAvatar } from "../../components/AgentAvatar";
import { useLightbox } from "../../components/ImageLightbox";
import { ActivityFeed } from "../../components/ActivityFeed";
import { useActiveBrand } from "../../brand-context";
import { deriveSteward } from "../../lib/agents";
import { ComplianceSection } from "./components/ComplianceSection";
import { CoverageSection } from "./components/CoverageSection";
import { AgentsPanel } from "./components/AgentsPanel";
import {
  PhoneHomePreview,
  TabPreview,
  LinkPreviewCard,
  BusinessCardPreview,
} from "../../components/Mockups";
import { computeBrandStats, relativeTime } from "../../lib/coverage";
import { resolveAccent } from "../../theme";
import { staggerContainer, staggerItem } from "../../lib/motion";
import type { AssetRecord } from "../../lib/types";

/** A copyable color chip: click to copy the hex, with a brief checkmark. */
function ColorChip({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}
      className="group flex items-center gap-3 rounded-xl border border-white/8 bg-white/[0.02] p-2 text-left transition-colors hover:bg-white/[0.05]"
      title={`Copy ${value}`}
    >
      <span
        className="h-10 w-10 shrink-0 rounded-lg ring-1 ring-white/10"
        style={{ background: value }}
      />
      <span className="min-w-0">
        <span className="block text-xs text-text-faint">{label}</span>
        <span className="flex items-center gap-1 font-mono text-xs text-text">
          {value}
          {copied ? (
            <Check size={12} className="text-accent" />
          ) : (
            <Copy size={12} className="text-text-faint opacity-0 group-hover:opacity-100" />
          )}
        </span>
      </span>
    </button>
  );
}

/**
 * Design tokens panel. Fetches the brand's portable tokens
 * (GET /brands/{slug}/tokens) and renders the color tokens (swatch + semantic
 * name + copyable hex) and font tokens, with a "Copy CSS" button that puts the
 * ready-to-paste tokens.css block on the clipboard.
 */
function TokensPanel({ slug }: { slug: string }) {
  const tokensQ = useQuery({
    queryKey: ["tokens", slug],
    queryFn: ({ signal }) => getBrandTokens(slug, signal),
    enabled: Boolean(slug),
  });
  const [copiedCss, setCopiedCss] = useState(false);

  const css = tokensQ.data?.css;
  const copyCss = () => {
    if (!css) return;
    void navigator.clipboard?.writeText(css);
    setCopiedCss(true);
    setTimeout(() => setCopiedCss(false), 1200);
  };

  return (
    <GlassPanel
      title={
        <span className="flex items-center gap-2">
          <Type size={15} className="text-accent" />
          Design tokens
        </span>
      }
      actions={
        <Button
          variant="ghost"
          size="sm"
          icon={copiedCss ? <Check size={13} /> : <Copy size={13} />}
          onClick={copyCss}
          disabled={!css}
        >
          {copiedCss ? "Copied" : "Copy CSS"}
        </Button>
      }
    >
      {tokensQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : tokensQ.isError || !tokensQ.data ? (
        <EmptyState
          title="Tokens unavailable"
          description={
            tokensQ.error instanceof Error
              ? tokensQ.error.message
              : "Could not load the brand's design tokens."
          }
        />
      ) : (
        <div className="space-y-5">
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-faint">
              Color
            </h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {tokensQ.data.color.map((tok) => (
                <ColorChip key={tok.name} label={tok.name} value={tok.value} />
              ))}
            </div>
          </div>
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-faint">
              Typography
            </h3>
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {tokensQ.data.typography.map((tok) => (
                <div
                  key={tok.name}
                  className="rounded-xl border border-white/8 bg-white/[0.02] p-2"
                >
                  <dt className="font-mono text-xs text-text-faint">{tok.name}</dt>
                  <dd
                    className="mt-0.5 text-sm text-text"
                    style={{ fontFamily: `"${tok.value}", sans-serif` }}
                  >
                    {tok.value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </GlassPanel>
  );
}

/** Find the first asset URL of any of the given types, in order of preference. */
function pickByType(assets: AssetRecord[], types: string[]): string | undefined {
  for (const t of types) {
    const hit = assets.find((a) => a.asset_type === t && a.url);
    if (hit?.url) return hit.url;
  }
  return undefined;
}

/**
 * Derive a slug from a display name: lowercase, non-alphanumerics collapsed to
 * single hyphens, edges trimmed. Matches the server's slug rule (lowercase,
 * digits, hyphens), so the auto-filled value validates as-is.
 */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * The "New sub-brand" control: a Button that opens an inline popover to stand up
 * a sub-brand of `sourceSlug`. The operator types a display name; the slug is
 * auto-derived (and stays editable). The new brand inherits this brand's
 * identity (as_child=true), so the form only collects the name and slug. On
 * success it invalidates the brands list and navigates to the new brand; a 409
 * (slug taken) and a 422 (bad slug) surface inline on the slug field.
 */
function NewSubBrandButton({
  sourceSlug,
  sourceName,
}: {
  sourceSlug: string;
  sourceName: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const ref = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState("");
  // Whether the operator has hand-edited the slug; until then it tracks the name.
  const [slugEdited, setSlugEdited] = useState(false);
  const [slug, setSlug] = useState("");

  const derivedSlug = slugEdited ? slug : slugify(displayName);

  const clone = useMutation({
    mutationFn: () =>
      cloneBrand(sourceSlug, {
        new_slug: derivedSlug,
        display_name: displayName.trim(),
        as_child: true,
      }),
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["brands"] });
      setOpen(false);
      navigate(`/brands/${encodeURIComponent(created.slug)}`);
    },
  });

  // Close on outside click / Escape, and reset the form each time it opens.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    const t = setTimeout(() => nameInputRef.current?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      clearTimeout(t);
    };
  }, [open]);

  function toggle() {
    setOpen((v) => {
      const next = !v;
      if (next) {
        setDisplayName("");
        setSlug("");
        setSlugEdited(false);
        clone.reset();
      }
      return next;
    });
  }

  // Map the clone error to a plain, field-level line. A 409 is a name clash, a
  // 422 a bad slug; anything else is a generic failure shown the same way.
  const error = clone.error;
  let slugError: string | null = null;
  let generalError: string | null = null;
  if (error instanceof ApiError) {
    if (error.status === 409) {
      slugError = `The slug "${derivedSlug}" is already taken. Pick another.`;
    } else if (error.status === 422) {
      slugError = "Use lowercase letters, digits, and hyphens only.";
    } else {
      generalError = error.message || "Could not create the sub-brand.";
    }
  } else if (error) {
    generalError = "Could not create the sub-brand.";
  }

  const canSubmit = displayName.trim().length > 0 && derivedSlug.length > 0;

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || clone.isPending) return;
    clone.mutate();
  }

  return (
    <div className="relative" ref={ref}>
      <Button variant="secondary" icon={<GitFork size={15} />} onClick={toggle}>
        New sub-brand
      </Button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: [0.22, 1, 0.36, 1] }}
            className="glass absolute right-0 z-50 mt-2 w-80 rounded-xl p-4 shadow-float"
          >
            <form onSubmit={submit} className="space-y-3">
              <div>
                <h3 className="font-display text-sm font-semibold text-text">
                  New sub-brand
                </h3>
                <p className="mt-0.5 text-xs text-text-muted">
                  Inherits {sourceName} identity (colors, type, and voice). You
                  can change anything after.
                </p>
              </div>

              <label className="block">
                <span className="mb-1 block text-xs font-medium text-text-faint">
                  Display name
                </span>
                <input
                  ref={nameInputRef}
                  type="text"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder={`${sourceName} Labs`}
                  className="w-full rounded-lg border border-white/10 bg-chrome-raised/70 px-3 py-2 text-sm text-text placeholder:text-text-faint focus:border-accent/50 focus:outline-none focus:ring-1 focus:ring-accent/50"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-xs font-medium text-text-faint">
                  Slug
                </span>
                <input
                  type="text"
                  value={derivedSlug}
                  onChange={(e) => {
                    setSlugEdited(true);
                    setSlug(slugify(e.target.value));
                  }}
                  placeholder="sub-brand-slug"
                  spellCheck={false}
                  className={`w-full rounded-lg border bg-chrome-raised/70 px-3 py-2 font-mono text-sm text-text placeholder:text-text-faint focus:outline-none focus:ring-1 ${
                    slugError
                      ? "border-red-500/50 focus:border-red-500/60 focus:ring-red-500/50"
                      : "border-white/10 focus:border-accent/50 focus:ring-accent/50"
                  }`}
                />
                {slugError ? (
                  <span className="mt-1 block text-xs text-red-300/90">
                    {slugError}
                  </span>
                ) : (
                  <span className="mt-1 block text-xs text-text-faint">
                    /brands/{derivedSlug || "…"}
                  </span>
                )}
              </label>

              {generalError && (
                <p className="text-xs text-red-300/90">{generalError}</p>
              )}

              <div className="flex justify-end gap-2 pt-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setOpen(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  loading={clone.isPending}
                  disabled={!canSubmit}
                  icon={<GitFork size={14} />}
                >
                  Create
                </Button>
              </div>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function BrandDetailPage() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const { setActiveSlug } = useActiveBrand();
  const { open: openLightbox } = useLightbox();

  const brandQ = useQuery({
    queryKey: ["brand", slug],
    queryFn: ({ signal }) => getBrand(slug, signal),
    enabled: Boolean(slug),
  });
  const assetsQ = useQuery({
    queryKey: ["assets", slug],
    queryFn: ({ signal }) => listAssets({ brand: slug }, signal),
    enabled: Boolean(slug),
  });
  const typesQ = useQuery({
    queryKey: ["asset-types"],
    queryFn: ({ signal }) => listAssetTypes(signal),
  });

  const assets = assetsQ.data ?? [];
  const stats = useMemo(
    () => computeBrandStats(slug, assets, typesQ.data ?? []),
    [slug, assets, typesQ.data],
  );
  // Split live identity from explorations. Concepts/drafts (and retired or
  // sent-back assets) go to a secondary shelf; everything else is production.
  const { production, concepts } = useMemo(() => {
    const nonProd = new Set(["concept", "changes_requested", "archived"]);
    const production = assets.filter((a) => !nonProd.has(a.lifecycle_status ?? ""));
    const concepts = assets.filter((a) => nonProd.has(a.lifecycle_status ?? ""));
    return { production, concepts };
  }, [assets]);

  const accent = resolveAccent(slug);
  // The brand's declared canonical mark is the source of truth for both the
  // hero lockup and the small icon mockups; fall back to guessing from the
  // (cross-mis-tagged) catalog only when the brand declares no mark.
  const mark = brandQ.data?.mark_url ?? undefined;
  const logo = mark ?? pickByType(assets, ["logo"]);
  const icon = mark ?? pickByType(assets, ["app_icon", "avatar", "logo"]);
  const og = pickByType(assets, ["og_image", "social_card"]);

  if (brandQ.isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner size={28} />
      </div>
    );
  }
  if (brandQ.isError || !brandQ.data) {
    return (
      <GlassPanel>
        <EmptyState
          title="Brand not found"
          description={`No brand matched "${slug}".`}
          action={
            <Link to="/">
              <Button variant="secondary" icon={<ArrowLeft size={15} />}>
                Back to Showcase
              </Button>
            </Link>
          }
        />
      </GlassPanel>
    );
  }

  const brand = brandQ.data;
  const steward = deriveSteward(brand);
  const colors = brand.visual.colors;
  const type = brand.visual.typography;
  const domain = `${slug.replace(/-/g, "")}.com`;

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="space-y-6"
    >
      <Link
        to="/"
        className="inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
      >
        <ArrowLeft size={15} /> Showcase
      </Link>

      {/* Hero. */}
      <motion.div variants={staggerItem}>
        <GlassPanel ring glow flush className="overflow-hidden">
          <div className="grid gap-0 sm:grid-cols-[1.1fr_1fr]">
            <div
              className="relative grid min-h-[220px] place-items-center p-8"
              style={{
                background: `linear-gradient(150deg, ${accent.primary}, ${accent.secondary})`,
              }}
            >
              <div className="absolute inset-0 bg-black/15" />
              {logo ? (
                <img
                  src={logo}
                  alt={`${brand.display_name} logo`}
                  onClick={() => openLightbox(logo, `${brand.display_name} logo`)}
                  className="relative max-h-24 max-w-[70%] cursor-zoom-in object-contain drop-shadow-xl"
                />
              ) : (
                <span className="relative font-display text-5xl font-semibold text-white/90">
                  {brand.display_name}
                </span>
              )}
            </div>
            <div className="flex flex-col justify-center gap-3 p-6">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h1 className="font-display text-2xl font-semibold tracking-tight text-text">
                    {brand.display_name}
                  </h1>
                  {brand.tagline && (
                    <p className="mt-1 text-sm text-text-muted">{brand.tagline}</p>
                  )}
                </div>
                <CoverageRing pct={stats.coveragePct} size={52} stroke={5} />
              </div>
              {brand.description && (
                <p className="text-sm leading-relaxed text-text-muted">
                  {brand.description}
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-text-faint">
                <span>{stats.total} assets</span>
                <span>·</span>
                <span>
                  {stats.presentTypes}/{stats.totalTypes} types
                </span>
                <span>·</span>
                <span>{relativeTime(stats.lastCreated)}</span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2">
                <Link to={`/brands/${encodeURIComponent(slug)}/guidelines`}>
                  <Button variant="primary" icon={<BookOpen size={15} />}>
                    Brand book
                  </Button>
                </Link>
                <Link to="/studio">
                  <Button variant="secondary" icon={<Sparkles size={15} />}>
                    Open in Studio
                  </Button>
                </Link>
                <NewSubBrandButton
                  sourceSlug={slug}
                  sourceName={brand.display_name}
                />
              </div>
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Brand agent: this brand's steward, embodied by its mark. Focusing the
          brand and opening Studio puts you in conversation with the steward. */}
      <motion.div variants={staggerItem}>
        <GlassPanel ring>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            {/* Prefer the page's resolved mark (it falls back to a catalog logo
                when the brand declares no mark) over the steward's raw mark_url. */}
            <AgentAvatar
              name={steward.name}
              src={mark ?? steward.avatarUrl}
              size={64}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
                <h2 className="font-display text-lg font-semibold tracking-tight text-text">
                  {steward.name}
                </h2>
                <span className="text-xs font-medium uppercase tracking-wider text-accent">
                  {steward.role}
                </span>
              </div>
              <p className="mt-1 max-w-xl text-sm leading-relaxed text-text-muted">
                Knows {steward.name} palette, rules, and assets. Generates and
                guards on-brand work.
              </p>
            </div>
            <div className="shrink-0">
              <Button
                variant="primary"
                icon={<Sparkles size={15} />}
                onClick={() => {
                  setActiveSlug(slug);
                  navigate("/studio");
                }}
              >
                Work with {steward.name} in Studio
              </Button>
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Palette + typography. */}
      <div className="grid gap-6 lg:grid-cols-2">
        <motion.div variants={staggerItem}>
          <GlassPanel title="Palette">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              <ColorChip label="Primary" value={colors.primary} />
              <ColorChip label="Accent" value={colors.accent} />
              <ColorChip label="Background" value={colors.background} />
              <ColorChip label="Foreground" value={colors.foreground} />
              <ColorChip label="Muted" value={colors.muted} />
            </div>
          </GlassPanel>
        </motion.div>

        <motion.div variants={staggerItem}>
          <GlassPanel title="Typography">
            <dl className="space-y-4">
              <div>
                <dt className="text-xs text-text-faint">Display · {type.display_font}</dt>
                <dd
                  className="mt-1 text-3xl font-semibold tracking-tight text-text"
                  style={{ fontFamily: `"${type.display_font}", sans-serif` }}
                >
                  {brand.display_name}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-text-faint">Body · {type.primary_font}</dt>
                <dd
                  className="mt-1 text-sm leading-relaxed text-text-muted"
                  style={{ fontFamily: `"${type.primary_font}", sans-serif` }}
                >
                  The quick brown fox jumps over the lazy dog. 0123456789
                </dd>
              </div>
              <div>
                <dt className="text-xs text-text-faint">Mono · {type.mono_font}</dt>
                <dd
                  className="mt-1 font-mono text-sm text-text-muted"
                  style={{ fontFamily: `"${type.mono_font}", monospace` }}
                >
                  brand-ops --generate {slug}
                </dd>
              </div>
            </dl>
          </GlassPanel>
        </motion.div>
      </div>

      {/* Design tokens: the palette and type expressed as portable, copyable
          tokens (sits with the palette, one step toward implementation). */}
      <motion.div variants={staggerItem}>
        <TokensPanel slug={slug} />
      </motion.div>

      {/* Agents: on-brand identities for this brand's agents (avatar + persona +
          manifest). A company's agents come out on-brand by construction. */}
      <motion.div variants={staggerItem}>
        <AgentsPanel slug={slug} brandName={brand.display_name} />
      </motion.div>

      {/* Brand rules: the machine-checkable truth. The seed of the compliance
          loop. Agents and the rest of the suite check assets against this. */}
      <motion.div variants={staggerItem}>
        <GlassPanel
          ring
          title={
            <span className="flex items-center gap-2">
              <ShieldCheck size={15} className="text-accent" />
              Brand rules
            </span>
          }
        >
          <p className="mb-4 max-w-2xl text-xs text-text-muted">
            The machine-checkable truth for this brand. Built-in and external
            agents (over MCP) and the rest of the suite check assets against
            these before they are approved.
          </p>

          <div className="grid gap-5 sm:grid-cols-3">
            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-faint">
                Allowed colors
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {[
                  colors.primary,
                  colors.accent,
                  colors.background,
                  colors.foreground,
                  colors.muted,
                ]
                  .filter((c): c is string => Boolean(c))
                  .map((c) => (
                    <span
                      key={c}
                      className="inline-flex items-center gap-1.5 rounded-full border border-white/8 bg-white/[0.02] py-0.5 pl-1 pr-2 font-mono text-[11px] text-text-muted"
                    >
                      <span
                        className="h-3.5 w-3.5 rounded-full ring-1 ring-white/10"
                        style={{ background: c }}
                      />
                      {c}
                    </span>
                  ))}
              </div>
            </div>

            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-text-faint">
                Voice
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {brand.voice.tone.length > 0 ? (
                  brand.voice.tone.map((t) => (
                    <span
                      key={t}
                      className="rounded-full border border-white/8 bg-white/[0.02] px-2 py-0.5 text-[11px] text-text-muted"
                    >
                      {t}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-text-faint">None defined</span>
                )}
              </div>
            </div>

            <div>
              <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-text-faint">
                <Ban size={12} className="text-red-400/80" /> Banned terms
              </h3>
              <div className="flex flex-wrap gap-1.5">
                {brand.voice.forbidden_phrases.length > 0 ? (
                  brand.voice.forbidden_phrases.map((p) => (
                    <span
                      key={p}
                      className="rounded-full border border-red-500/25 bg-red-500/10 px-2 py-0.5 text-[11px] text-red-300/90"
                    >
                      {p}
                    </span>
                  ))
                ) : (
                  <span className="text-xs text-text-faint">None defined</span>
                )}
              </div>
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Coverage checklist: the per-type gaps, each with an inline Generate
          and a Studio deep-link, plus a Generate-full-kit by the ring. */}
      <motion.div variants={staggerItem}>
        <CoverageSection slug={slug} stats={stats} />
      </motion.div>

      {/* Compliance gate: every catalog asset checked against the brand rules. */}
      <motion.div variants={staggerItem}>
        <ComplianceSection slug={slug} />
      </motion.div>

      {/* Activity: the provenance log for this brand. Who (person or agent)
          did what, newest first. A secondary, ambient surface. */}
      <motion.div variants={staggerItem}>
        <GlassPanel
          title={
            <span className="flex items-center gap-2">
              <Activity size={15} className="text-accent" />
              Activity
            </span>
          }
        >
          <ActivityFeed brand={slug} limit={20} />
        </GlassPanel>
      </motion.div>

      {/* Live mockups. */}
      <motion.div variants={staggerItem}>
        <GlassPanel title="In context">
          <div className="flex flex-wrap items-start gap-8">
            <PhoneHomePreview icon={icon} label={brand.display_name} />
            <div className="flex flex-col gap-6">
              <TabPreview favicon={icon} title={brand.display_name} domain={domain} />
              <LinkPreviewCard
                ogImage={og}
                domain={domain}
                title={brand.display_name}
                description={brand.tagline ?? brand.description ?? ""}
              />
              <BusinessCardPreview
                logo={logo}
                name={brand.display_name}
                tagline={brand.tagline}
              />
            </div>
          </div>
        </GlassPanel>
      </motion.div>

      {/* Asset strip, split into production vs concepts/drafts so the live
          identity reads clean and the explorations stay visible but secondary. */}
      <motion.div variants={staggerItem}>
        <GlassPanel title={`Assets (${production.length})`}>
          {assetsQ.isLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : production.length === 0 ? (
            <EmptyState
              title="No assets yet"
              description="Generate or upload assets for this brand to see them here."
            />
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {production.slice(0, 20).map((a, i) => (
                <AssetThumb key={`${a.file}-${i}`} asset={a} src={a.url ?? undefined} />
              ))}
            </div>
          )}
        </GlassPanel>
      </motion.div>

      {/* Concepts & drafts: things we made and explored but did not put into
          production. Collapsed by default, clearly labeled, visually muted. */}
      {concepts.length > 0 && (
        <motion.div variants={staggerItem}>
          <details className="glass overflow-hidden rounded-2xl shadow-panel">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 text-sm font-medium text-text-muted hover:text-text">
              <FlaskConical size={15} className="text-text-faint" />
              Concepts &amp; drafts
              <span className="text-xs text-text-faint">
                {concepts.length} not approved for production
              </span>
              <ChevronRight size={15} className="ml-auto text-text-faint" />
            </summary>
            <div className="border-t border-white/8 px-5 pb-5 pt-4">
              <p className="mb-3 text-xs text-text-faint">
                Made and explored, kept for reference. Not in use and not approved
                for production.
              </p>
              <div className="grid grid-cols-2 gap-3 opacity-80 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {concepts.map((a, i) => (
                  <AssetThumb key={`${a.file}-${i}`} asset={a} src={a.url ?? undefined} />
                ))}
              </div>
            </div>
          </details>
        </motion.div>
      )}
    </motion.div>
  );
}
