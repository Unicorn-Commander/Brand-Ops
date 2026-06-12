import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Ban,
  ImageIcon,
  Tag,
  Type as TypeIcon,
} from "lucide-react";
import { api } from "../../../api/client";
import { Spinner } from "../../../components/Spinner";
import { ColorSwatches } from "./ColorSwatches";
import { PromptPreviewWidget } from "./PromptPreviewWidget";
import type { BrandDetail } from "../../../lib/types";

/**
 * The identity panel for one brand: its colors, fonts, tagline, description,
 * voice, style keywords, and reference-image roles, plus the read-only prompt
 * preview widget. Fetches GET /api/brands/{slug} itself, keyed by slug, so the
 * page only has to hand it the selected slug.
 */

/** A labeled section header used down the identity panel. */
function SectionHeading({
  icon,
  children,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
      {icon}
      {children}
    </h3>
  );
}

/** One bordered block separating a section from the next. */
function Section({
  title,
  icon,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-chrome-border px-5 py-5">
      <SectionHeading icon={icon}>{title}</SectionHeading>
      {children}
    </div>
  );
}

function Chip({
  children,
  tone = "default",
}: {
  children: React.ReactNode;
  tone?: "default" | "muted" | "danger";
}) {
  const toneClass =
    tone === "danger"
      ? "border-red-500/30 bg-red-500/10 text-red-300"
      : tone === "muted"
        ? "border-chrome-border bg-chrome-bg text-text-muted"
        : "border-accent/30 bg-accent/10 text-text";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs ${toneClass}`}
    >
      {children}
    </span>
  );
}

function FontRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-chrome-border bg-chrome-raised px-3 py-2">
      <span className="text-xs text-text-muted">{label}</span>
      <span className="truncate text-sm text-text" style={{ fontFamily: value }}>
        {value}
      </span>
    </div>
  );
}

export function BrandIdentity({ slug }: { slug: string }) {
  const {
    data: brand,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ["brand", slug],
    queryFn: ({ signal }) => api.getBrand(slug, signal),
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 px-5 py-16 text-sm text-text-muted">
        <Spinner size={18} />
        Loading brand
      </div>
    );
  }

  if (isError || !brand) {
    return (
      <div className="flex items-start gap-2 px-5 py-16 text-sm text-red-300">
        <AlertTriangle size={16} className="mt-0.5 shrink-0" />
        <span>
          Could not load this brand.
          {error instanceof Error ? ` ${error.message}` : ""}
        </span>
      </div>
    );
  }

  return <IdentityBody brand={brand} />;
}

function IdentityBody({ brand }: { brand: BrandDetail }) {
  const { visual, voice } = brand;
  return (
    <div>
      {/* Header */}
      <div className="px-5 py-5">
        <h2 className="text-lg font-semibold text-text">{brand.display_name}</h2>
        <p className="mt-0.5 font-mono text-xs text-text-faint">{brand.slug}</p>
        {brand.tagline && (
          <p className="mt-2 text-sm text-text-muted">{brand.tagline}</p>
        )}
        {brand.parent && (
          <p className="mt-1 text-xs text-text-faint">Sub-brand</p>
        )}
        {brand.description && (
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-text-muted">
            {brand.description}
          </p>
        )}
      </div>

      <Section title="Colors">
        <ColorSwatches colors={visual.colors} />
      </Section>

      <Section title="Typography" icon={<TypeIcon size={13} />}>
        <div className="grid gap-2 sm:grid-cols-3">
          <FontRow label="Primary" value={visual.typography.primary_font} />
          <FontRow label="Display" value={visual.typography.display_font} />
          <FontRow label="Mono" value={visual.typography.mono_font} />
        </div>
      </Section>

      {visual.style_keywords.length > 0 && (
        <Section title="Style keywords" icon={<Tag size={13} />}>
          <div className="flex flex-wrap gap-2">
            {visual.style_keywords.map((kw) => (
              <Chip key={kw}>{kw}</Chip>
            ))}
          </div>
        </Section>
      )}

      <Section title="Voice">
        <div className="space-y-4">
          <div>
            <p className="mb-2 text-xs text-text-muted">Tone</p>
            {voice.tone.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {voice.tone.map((t) => (
                  <Chip key={t}>{t}</Chip>
                ))}
              </div>
            ) : (
              <p className="text-sm text-text-faint">Not set.</p>
            )}
          </div>
          {voice.forbidden_phrases.length > 0 && (
            <div>
              <p className="mb-2 text-xs text-text-muted">Avoid</p>
              <div className="flex flex-wrap gap-2">
                {voice.forbidden_phrases.map((p) => (
                  <Chip key={p} tone="danger">
                    <Ban size={11} />
                    {p}
                  </Chip>
                ))}
              </div>
            </div>
          )}
        </div>
      </Section>

      <Section title="Reference images" icon={<ImageIcon size={13} />}>
        {brand.reference_roles.length > 0 ? (
          <>
            <div className="flex flex-wrap gap-2">
              {brand.reference_roles.map((role) => (
                <Chip key={role} tone="muted">
                  <ImageIcon size={11} />
                  {role}
                </Chip>
              ))}
            </div>
            <p className="mt-2 text-xs text-text-faint">
              These reference roles guide generation. The API lists the roles; it
              does not serve the image files.
            </p>
          </>
        ) : (
          <p className="text-sm text-text-faint">
            No reference images for this brand.
          </p>
        )}
      </Section>

      <Section title="Preview a prompt">
        <PromptPreviewWidget
          brandSlug={brand.slug}
          promptAssets={brand.prompt_assets}
        />
      </Section>
    </div>
  );
}
