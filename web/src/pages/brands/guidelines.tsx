/**
 * Brand guidelines: the living brand book for one slug. This is the canonical
 * brand deliverable, a long sectioned document an agency hands a client and an
 * in-house team treats as the brand source of truth.
 *
 * It pulls the brand's assembled guidelines (GET /api/brands/{slug}/guidelines:
 * identity, design tokens, the rules manifest, and assets grouped by type) and
 * lays them out as Cover, Logo and usage, Color system, Typography, Voice and
 * tone, and Assets. On screen it stays in the app's dark, brand-accented theme;
 * Export prints it through window.print(), where the scoped print rules in
 * index.css flip ONLY the brand book to a clean white page (the app chrome and
 * the aurora are hidden) for a tidy PDF.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Ban,
  BookOpen,
  Check,
  Copy,
  Printer,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { getBrandGuidelines } from "../../api/client";
import { GlassPanel } from "../../components/GlassPanel";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { AssetThumb } from "../../components/AssetThumb";
import { applyBrandAccent, resolveAccent } from "../../theme";
import { staggerContainer, staggerItem } from "../../lib/motion";
import type {
  AssetGroup,
  BrandGuidelines,
  ColorToken,
  FontToken,
} from "../../lib/types";

/**
 * A section shell: a quiet numbered eyebrow, a display title, an optional lede,
 * and the section body. Each section is a stagger child and is allowed to break
 * onto its own printed page (the print rules read `.gl-section`).
 */
function Section({
  index,
  eyebrow,
  title,
  lede,
  children,
}: {
  index: string;
  eyebrow: string;
  title: string;
  lede?: string;
  children: React.ReactNode;
}) {
  return (
    <motion.section variants={staggerItem} className="gl-section">
      <header className="mb-5 border-b border-white/8 pb-4 print:border-black/10">
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-xs tabular-nums text-text-faint print:text-black/40">
            {index}
          </span>
          <span className="text-xs font-medium uppercase tracking-[0.2em] text-accent">
            {eyebrow}
          </span>
        </div>
        <h2 className="mt-2 font-display text-2xl font-semibold tracking-tight text-text print:text-black">
          {title}
        </h2>
        {lede && (
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-text-muted print:text-black/70">
            {lede}
          </p>
        )}
      </header>
      {children}
    </motion.section>
  );
}

/** Format an RGB triple as `rgb(r, g, b)` for the swatch caption. */
function rgbString(rgb: number[]): string {
  if (rgb.length !== 3) return "";
  return `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})`;
}

/** A checkerboard so light or near-background swatches stay visible on dark chrome. */
const CHECKER =
  "repeating-conic-gradient(#2a2a3d 0% 25%, #1b1b27 0% 50%) 50% / 14px 14px";

/**
 * A large color-token card: the swatch over a checkerboard, the semantic token
 * name, and the hex + rgb. Clicking copies the hex (a brief checkmark confirms).
 * Kept legible in print (the caption flips to dark text on white).
 */
function ColorTokenCard({ token }: { token: ColorToken }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token.value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard can be unavailable (insecure context); fail quietly.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={`Copy ${token.value}`}
      className="gl-swatch group flex flex-col overflow-hidden rounded-2xl border border-white/8 bg-white/[0.02] text-left transition-colors hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 print:border-black/10 print:bg-transparent"
    >
      <span
        className="relative block h-28 w-full"
        style={{ background: CHECKER }}
      >
        <span className="absolute inset-0" style={{ backgroundColor: token.value }} />
        <span className="absolute right-2 top-2 rounded-md bg-black/40 p-1 text-white opacity-0 transition-opacity group-hover:opacity-100 print:hidden">
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </span>
      </span>
      <span className="flex flex-col gap-0.5 p-3">
        <span className="font-mono text-xs text-text-faint print:text-black/50">
          {token.name}
        </span>
        <span className="font-mono text-sm uppercase text-text print:text-black">
          {token.value}
        </span>
        {token.rgb.length === 3 && (
          <span className="font-mono text-xs text-text-muted print:text-black/60">
            {rgbString(token.rgb)}
          </span>
        )}
      </span>
    </button>
  );
}

/**
 * A type specimen: a large display line set in the token's family, the token
 * name and family beneath, and a short paragraph in the same family so the
 * reader sees the face at both heading and body size.
 */
function TypeSpecimen({ token }: { token: FontToken }) {
  const family = `"${token.value}", sans-serif`;
  return (
    <div className="gl-specimen rounded-2xl border border-white/8 bg-white/[0.02] p-6 print:border-black/10 print:bg-transparent">
      <p
        className="text-4xl font-semibold leading-tight tracking-tight text-text print:text-black"
        style={{ fontFamily: family }}
      >
        Aa Bb Cc Dd
      </p>
      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-white/8 pt-3 print:border-black/10">
        <span className="font-mono text-xs text-text-faint print:text-black/50">
          {token.name}
        </span>
        <span className="text-sm font-medium text-text print:text-black">
          {token.value}
        </span>
      </div>
      <p
        className="mt-3 text-sm leading-relaxed text-text-muted print:text-black/70"
        style={{ fontFamily: family }}
      >
        The quick brown fox jumps over the lazy dog. 0123456789. Set in{" "}
        {token.value} for the body specimen. Pack my box with five dozen liquor jugs.
      </p>
    </div>
  );
}

/**
 * One asset group as a titled subsection: the type label, the count, and a tidy
 * grid of that group's thumbnails (served by their catalog URL).
 */
function AssetGroupBlock({ group }: { group: AssetGroup }) {
  return (
    <div className="gl-asset-group">
      <div className="mb-3 flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-text print:text-black">
          {group.label}
        </h3>
        <span className="text-xs tabular-nums text-text-faint print:text-black/50">
          {group.count}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
        {group.items.map((a, i) => (
          <AssetThumb key={`${a.file}-${i}`} asset={a} src={a.url ?? undefined} />
        ))}
      </div>
    </div>
  );
}

/** A logo plate: the mark centered on one surface (light or dark) with clear-space framing. */
function LogoPlate({
  src,
  name,
  surface,
}: {
  src: string | undefined;
  name: string;
  surface: "light" | "dark";
}) {
  const isLight = surface === "light";
  return (
    <div className="flex flex-col gap-2">
      <div
        className={[
          "grid aspect-[4/3] place-items-center rounded-2xl border p-10",
          isLight
            ? "border-black/10 bg-white"
            : "border-white/10 bg-[#0a0a0f]",
        ].join(" ")}
      >
        {src ? (
          <img
            src={src}
            alt={`${name} mark on a ${surface} surface`}
            className="max-h-24 max-w-[70%] object-contain"
          />
        ) : (
          <span
            className={[
              "font-display text-3xl font-semibold tracking-tight",
              isLight ? "text-black/80" : "text-white/90",
            ].join(" ")}
          >
            {name}
          </span>
        )}
      </div>
      <p className="text-xs text-text-faint print:text-black/50">
        {isLight ? "On light" : "On dark"}
      </p>
    </div>
  );
}

/**
 * The printable brand book. Receives the loaded guidelines and renders the full
 * document. The outer wrapper carries `id="brand-book"` so the print rules can
 * isolate it (everything else on the page is hidden when printing).
 */
function BrandBook({ data, slug }: { data: BrandGuidelines; slug: string }) {
  const { brand, colors, typography, rules, voice, logo, asset_groups, generated_at } =
    data;

  // The cover gradient uses the brand's own primary/accent color tokens when
  // present; otherwise it falls back to the slug's built-in suite pairing (the
  // same accent the page applies on mount).
  const accent = useMemo(() => {
    const byName = (name: string) =>
      colors.find((t) => t.name === name)?.value;
    const primary = byName("brand/primary");
    const secondary = byName("brand/accent");
    if (primary && secondary) return { primary, secondary };
    return resolveAccent(slug);
  }, [colors, slug]);

  const printedDate = useMemo(() => {
    const iso = generated_at;
    const d = iso ? new Date(iso) : new Date();
    if (Number.isNaN(d.getTime())) return new Date().toLocaleDateString();
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  }, [generated_at]);

  const mark = logo.mark_url ?? brand.mark_url ?? undefined;
  const hasColorTokens = colors.length > 0;
  const hasFontTokens = typography.length > 0;
  const hasAssets = asset_groups.length > 0;
  // The lockups grid (logo-type assets) beyond the mark itself.
  const lockups = logo.lockups ?? [];

  return (
    <motion.div
      id="brand-book"
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="space-y-12"
    >
      {/* COVER. Full-bleed brand-accent gradient with the mark, the name, the
          tagline, a quiet eyebrow, and the Export action. */}
      <motion.section variants={staggerItem} className="gl-section gl-cover">
        <GlassPanel ring glow flush className="overflow-hidden">
          <div
            className="relative grid min-h-[360px] place-items-center px-8 py-16 text-center"
            style={{
              background: `linear-gradient(150deg, ${accent.primary}, ${accent.secondary})`,
            }}
          >
            <div className="absolute inset-0 bg-black/15 print:hidden" />
            {/* Export sits top-right, hidden from the printed page itself. */}
            <div className="absolute right-4 top-4 print:hidden">
              <Button
                variant="secondary"
                size="sm"
                icon={<Printer size={14} />}
                onClick={() => window.print()}
                className="border-white/30 bg-white/15 text-white backdrop-blur hover:bg-white/25"
              >
                Export
              </Button>
            </div>
            <div className="relative flex flex-col items-center gap-5">
              {mark && (
                <img
                  src={mark}
                  alt={`${brand.display_name} mark`}
                  className="max-h-28 max-w-[60%] object-contain drop-shadow-xl"
                />
              )}
              <span className="text-xs font-medium uppercase tracking-[0.3em] text-white/75">
                Brand guidelines
              </span>
              <h1 className="font-display text-5xl font-semibold tracking-tight text-white drop-shadow-sm">
                {brand.display_name}
              </h1>
              {brand.tagline && (
                <p className="max-w-xl text-base text-white/85">{brand.tagline}</p>
              )}
            </div>
          </div>
        </GlassPanel>
        {brand.description && (
          <p className="mt-6 max-w-2xl text-sm leading-relaxed text-text-muted print:text-black/70">
            {brand.description}
          </p>
        )}
      </motion.section>

      {/* LOGO AND USAGE. */}
      <Section
        index="01"
        eyebrow="Identity"
        title="Logo and usage"
        lede="The mark is the brand's signature. Give it room to breathe, keep it legible at small sizes, and use the lockups as supplied."
      >
        <div className="space-y-8">
          <div className="grid gap-6 sm:grid-cols-2">
            <LogoPlate src={mark} name={brand.display_name} surface="light" />
            <LogoPlate src={mark} name={brand.display_name} surface="dark" />
          </div>

          {/* The lockups: every logo-type asset the brand has on file. With just
              one logo this stays quiet (the plates above carry the mark). */}
          {lockups.length > 0 && (
            <div className="gl-asset-group">
              <h3 className="mb-3 flex items-baseline gap-2 text-sm font-semibold text-text print:text-black">
                Lockups
                <span className="text-xs tabular-nums text-text-faint print:text-black/50">
                  {lockups.length}
                </span>
              </h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {lockups.map((a, i) => (
                  <AssetThumb
                    key={`${a.file}-${i}`}
                    asset={a}
                    src={a.url ?? undefined}
                  />
                ))}
              </div>
            </div>
          )}

          <div className="grid gap-5 sm:grid-cols-3">
            <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-4 print:border-black/10 print:bg-transparent">
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-faint print:text-black/50">
                Clear space
              </h3>
              <p className="text-sm text-text-muted print:text-black/70">
                {rules.manual_rules.find((r) => r.key === "logo_clear_space")?.note ??
                  "Keep clear space around the mark equal to the mark's height."}
              </p>
            </div>
            <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-4 print:border-black/10 print:bg-transparent">
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-faint print:text-black/50">
                Minimum size
              </h3>
              <p className="text-sm text-text-muted print:text-black/70">
                {rules.manual_rules.find((r) => r.key === "logo_min_size")?.note ??
                  "Do not render the mark below its minimum size."}
              </p>
            </div>
            <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-4 print:border-black/10 print:bg-transparent">
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wider text-text-faint print:text-black/50">
                Contrast
              </h3>
              <p className="text-sm text-text-muted print:text-black/70">
                Place the mark on light or dark surfaces with clear contrast, as
                shown above. Avoid busy backgrounds that reduce legibility.
              </p>
            </div>
          </div>

          {/* Any other manual rules beyond the two we surface above. */}
          {rules.manual_rules.filter(
            (r) => r.key !== "logo_clear_space" && r.key !== "logo_min_size",
          ).length > 0 && (
            <ul className="space-y-1.5">
              {rules.manual_rules
                .filter(
                  (r) => r.key !== "logo_clear_space" && r.key !== "logo_min_size",
                )
                .map((r) => (
                  <li
                    key={r.key}
                    className="flex items-start gap-2 text-sm text-text-muted print:text-black/70"
                  >
                    <Check size={14} className="mt-0.5 shrink-0 text-accent" />
                    {r.note}
                  </li>
                ))}
            </ul>
          )}
        </div>
      </Section>

      {/* COLOR SYSTEM. */}
      <Section
        index="02"
        eyebrow="Color"
        title="Color system"
        lede="These are the brand's allowed colors. Generated and uploaded assets are checked against this palette before they are approved, so use these values exactly."
      >
        {hasColorTokens ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {colors.map((tok) => (
              <ColorTokenCard key={tok.name} token={tok} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-text-muted print:text-black/70">
            This brand defines no color tokens.
          </p>
        )}
      </Section>

      {/* TYPOGRAPHY. */}
      <Section
        index="03"
        eyebrow="Type"
        title="Typography"
        lede="The typefaces that carry the brand's voice. Use the display face for headlines and the body face for running text."
      >
        {hasFontTokens ? (
          <div className="space-y-5">
            {typography.map((tok) => (
              <TypeSpecimen key={tok.name} token={tok} />
            ))}
          </div>
        ) : (
          <p className="text-sm text-text-muted print:text-black/70">
            This brand defines no type tokens.
          </p>
        )}
      </Section>

      {/* VOICE AND TONE. */}
      <Section
        index="04"
        eyebrow="Voice"
        title="Voice and tone"
        lede="How the brand sounds in writing. Keep copy plain and direct."
      >
        <div className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-5 print:border-black/10 print:bg-transparent">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-text-faint print:text-black/50">
              <ShieldCheck size={13} className="text-accent" /> Tone
            </h3>
            {voice.tone.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {voice.tone.map((t) => (
                  <span
                    key={t}
                    className="rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-sm text-accent print:border-black/15 print:bg-transparent print:text-black"
                  >
                    {t}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-text-muted print:text-black/70">
                No tone defined.
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-white/8 bg-white/[0.02] p-5 print:border-black/10 print:bg-transparent">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-text-faint print:text-black/50">
              <Ban size={13} className="text-red-400/80" /> Words we avoid
            </h3>
            {voice.forbidden_phrases.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {voice.forbidden_phrases.map((p) => (
                  <span
                    key={p}
                    className="rounded-full border border-red-500/25 bg-red-500/10 px-3 py-1 text-sm text-red-300/90 print:border-red-700/40 print:bg-transparent print:text-red-700"
                  >
                    {p}
                  </span>
                ))}
              </div>
            ) : (
              <p className="text-sm text-text-muted print:text-black/70">
                No forbidden phrases defined.
              </p>
            )}
          </div>
        </div>
      </Section>

      {/* ASSETS. */}
      <Section
        index="05"
        eyebrow="Library"
        title="Assets"
        lede="The brand's produced assets, grouped by type. Each is on-brand and ready to use."
      >
        {hasAssets ? (
          <div className="space-y-8">
            {asset_groups.map((group) => (
              <AssetGroupBlock key={group.asset_type} group={group} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="No assets yet"
            description="Generate or upload assets for this brand and they will appear here, grouped by type."
          />
        )}
      </Section>

      {/* Printed footer line: brand + date. Hidden on screen, shown in print. */}
      <footer className="hidden border-t border-black/10 pt-4 text-xs text-black/50 print:block">
        {brand.display_name} brand guidelines &middot; {printedDate}
      </footer>
    </motion.div>
  );
}

export default function BrandGuidelinesPage() {
  const { slug = "" } = useParams();

  // Theme the document in the brand's accent on mount (and whenever the slug
  // changes), so the page paints in the brand's accent immediately, matching
  // how the brand sheet and the rest of the app present a focused brand.
  useEffect(() => {
    if (slug) applyBrandAccent(slug);
  }, [slug]);

  const guidelinesQ = useQuery({
    queryKey: ["guidelines", slug],
    queryFn: ({ signal }) => getBrandGuidelines(slug, signal),
    enabled: Boolean(slug),
  });

  // Re-assert the accent from the loaded brand's OWN color tokens once the data
  // arrives. This both themes an arbitrary (non-suite) brand from its real
  // colors and wins the mount-order race against the app-wide BrandProvider,
  // whose effect (an ancestor) runs after this page's mount effect and would
  // otherwise leave the previously-active brand's accent applied.
  const loadedColors = guidelinesQ.data?.colors;
  useEffect(() => {
    if (!loadedColors || loadedColors.length === 0) return;
    const byName = (name: string) =>
      loadedColors.find((t) => t.name === name)?.value ?? null;
    applyBrandAccent({
      primary: byName("brand/primary") ?? "",
      accent: byName("brand/accent") ?? "",
      background: byName("surface/background"),
      foreground: byName("text/foreground"),
      muted: byName("text/muted"),
    });
  }, [loadedColors]);

  if (guidelinesQ.isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner size={28} />
      </div>
    );
  }

  if (guidelinesQ.isError || !guidelinesQ.data) {
    return (
      <GlassPanel>
        <EmptyState
          icon={<BookOpen size={26} />}
          title="Guidelines unavailable"
          description={
            guidelinesQ.error instanceof Error
              ? guidelinesQ.error.message
              : `Could not load the brand book for "${slug}".`
          }
          action={
            <Link to={`/brands/${encodeURIComponent(slug)}`}>
              <Button variant="secondary" icon={<ArrowLeft size={15} />}>
                Back to brand
              </Button>
            </Link>
          }
        />
      </GlassPanel>
    );
  }

  const data = guidelinesQ.data;

  return (
    <div className="space-y-6">
      {/* On-screen back link + open-in-Studio. Both are chrome, hidden in print. */}
      <div className="flex items-center justify-between gap-3 print:hidden">
        <Link
          to={`/brands/${encodeURIComponent(slug)}`}
          className="inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
        >
          <ArrowLeft size={15} /> {data.brand.display_name}
        </Link>
        <Link to="/studio">
          <Button variant="ghost" size="sm" icon={<Sparkles size={14} />}>
            Open in Studio
          </Button>
        </Link>
      </div>

      <BrandBook data={data} slug={slug} />
    </div>
  );
}
