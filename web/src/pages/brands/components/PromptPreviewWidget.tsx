import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Eye, AlertTriangle } from "lucide-react";
import { api } from "../../../api/client";
import { ApiError } from "../../../api/client";
import { Button } from "../../../components/Button";
import { Spinner } from "../../../components/Spinner";
import type { AssetType, PromptPreview } from "../../../lib/types";

/**
 * A read-only "preview a prompt" widget. Pick an asset type (and optionally a
 * topic) and it calls POST /api/prompt-preview to show exactly what prompt this
 * brand would compose for that asset. It never generates and never spends: the
 * preview endpoint is a dry run that returns the composed prompt and plan.
 */

interface PromptPreviewWidgetProps {
  brandSlug: string;
  /** Asset-type keys this brand has prompt templates for, surfaced as hints. */
  promptAssets: string[];
}

/** Pull the API's message off an error, falling back to a plain string. */
function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Could not build the preview.";
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-xs">
      <span className="text-text-muted">{label}</span>
      <span className="font-mono text-text">{value}</span>
    </div>
  );
}

export function PromptPreviewWidget({
  brandSlug,
  promptAssets,
}: PromptPreviewWidgetProps) {
  const [asset, setAsset] = useState("");
  const [topic, setTopic] = useState("");
  // Holds the request that produced the current preview, so a result clears
  // itself when the brand changes underneath it (different brandSlug prop).
  const [preview, setPreview] = useState<PromptPreview | null>(null);
  const [previewBrand, setPreviewBrand] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const { data: assetTypes = [], isLoading: typesLoading } = useQuery({
    queryKey: ["asset-types"],
    queryFn: ({ signal }) => api.listAssetTypes(signal),
    staleTime: 5 * 60 * 1000,
  });

  // Sort the brand's own asset types first so the relevant ones lead.
  const orderedTypes = orderAssetTypes(assetTypes, promptAssets);

  const stale = preview !== null && previewBrand !== brandSlug;
  const result = stale ? null : preview;

  async function run() {
    if (!asset) return;
    setRunning(true);
    setError(null);
    try {
      const trimmedTopic = topic.trim();
      const out = await api.promptPreview({
        brand: brandSlug,
        asset,
        topic: trimmedTopic ? trimmedTopic : null,
      });
      setPreview(out);
      setPreviewBrand(brandSlug);
    } catch (err) {
      setPreview(null);
      setPreviewBrand(null);
      setError(errorMessage(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-text-muted">
        Pick an asset type to see the prompt this brand would compose. This is a
        dry run. It does not generate anything.
      </p>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <label className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="text-xs font-medium text-text-muted">Asset type</span>
          <select
            value={asset}
            onChange={(e) => setAsset(e.target.value)}
            disabled={typesLoading}
            className="h-10 rounded-lg border border-chrome-border bg-chrome-raised px-3 text-sm text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:opacity-50"
          >
            <option value="">
              {typesLoading ? "Loading asset types" : "Select an asset type"}
            </option>
            {orderedTypes.map((type) => (
              <option key={type.key} value={type.key}>
                {type.label}
                {promptAssets.includes(type.key) ? " (this brand)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="text-xs font-medium text-text-muted">
            Topic (optional)
          </span>
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="e.g. spring launch"
            className="h-10 rounded-lg border border-chrome-border bg-chrome-raised px-3 text-sm text-text placeholder:text-text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
          />
        </label>

        <Button
          variant="primary"
          icon={<Eye size={15} />}
          onClick={run}
          loading={running}
          disabled={!asset}
        >
          Preview
        </Button>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {running && !result && (
        <div className="flex items-center gap-2 text-sm text-text-muted">
          <Spinner size={16} />
          Building the prompt
        </div>
      )}

      {result && <PreviewResult preview={result} />}
    </div>
  );
}

/** Render a finished preview: the composed prompt plus its plan and warnings. */
function PreviewResult({ preview }: { preview: PromptPreview }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-chrome-border bg-chrome-bg">
        <header className="border-b border-chrome-border px-3 py-2 text-xs font-medium text-text-muted">
          Composed prompt
        </header>
        <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-3 py-2.5 font-mono text-xs leading-relaxed text-text">
          {preview.prompt}
        </pre>
      </div>

      <div className="grid gap-x-6 gap-y-1.5 rounded-lg border border-chrome-border bg-chrome-raised px-3 py-2.5 sm:grid-cols-2">
        <MetaRow label="Backend" value={preview.backend} />
        <MetaRow
          label="Size"
          value={`${preview.width} × ${preview.height}`}
        />
        <MetaRow
          label="Variant"
          value={preview.variant ?? "default"}
        />
        <MetaRow
          label="Transparent"
          value={preview.transparent ? "yes" : "no"}
        />
        <MetaRow
          label="Reference images"
          value={String(preview.reference_images.length)}
        />
        <MetaRow label="Plans to write" value={preview.planned_path} />
      </div>

      {preview.missing_refs.length > 0 && (
        <Notice title="Missing reference images" items={preview.missing_refs} />
      )}
      {preview.warnings.length > 0 && (
        <Notice title="Warnings" items={preview.warnings} />
      )}
    </div>
  );
}

/** A small amber callout listing prompt-plan warnings or missing references. */
function Notice({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
      <p className="mb-1 flex items-center gap-1.5 font-medium">
        <AlertTriangle size={13} /> {title}
      </p>
      <ul className="list-disc space-y-0.5 pl-5">
        {items.map((item, i) => (
          <li key={`${item}-${i}`} className="font-mono break-all">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Brand's own asset types first (in promptAssets order), then the rest. */
function orderAssetTypes(
  types: AssetType[],
  promptAssets: string[],
): AssetType[] {
  if (promptAssets.length === 0) return types;
  const rank = new Map(promptAssets.map((key, i) => [key, i]));
  return [...types].sort((a, b) => {
    const ra = rank.has(a.key) ? (rank.get(a.key) as number) : Number.MAX_SAFE_INTEGER;
    const rb = rank.has(b.key) ? (rank.get(b.key) as number) : Number.MAX_SAFE_INTEGER;
    return ra - rb;
  });
}
