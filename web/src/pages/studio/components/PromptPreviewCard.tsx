import { useState } from "react";
import {
  Wand2,
  Copy,
  Check,
  AlertTriangle,
  ImageOff,
  Layers,
} from "lucide-react";
import { Button } from "../../../components/Button";
import { Spinner } from "../../../components/Spinner";
import type { PromptPreview } from "../../../lib/types";

interface PromptPreviewCardProps {
  preview: PromptPreview | null;
  /** True while a preview request is in flight. */
  loading: boolean;
  /** A preview-fetch error message, if the last attempt failed. */
  error: string | null;
  /** How many variations to request on generate (1..8). */
  count: number;
  onCountChange: (n: number) => void;
  /** True while a live generate is running. */
  generating: boolean;
  onGenerate: () => void;
}

function Pill({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-white/12 bg-chrome-raised/90 px-2 py-0.5 text-xs font-medium text-text">
      {children}
    </span>
  );
}

/**
 * The composed-prompt card. Shows the prompt the engine will use, the plan
 * (backend, dimensions, transparency), and any missing references or warnings
 * the preview reported, with a Generate action and a variation count.
 */
export function PromptPreviewCard({
  preview,
  loading,
  error,
  count,
  onCountChange,
  generating,
  onGenerate,
}: PromptPreviewCardProps) {
  const [copied, setCopied] = useState(false);

  const copyPrompt = async () => {
    if (!preview) return;
    try {
      await navigator.clipboard.writeText(preview.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked; ignore rather than disrupt the flow.
    }
  };

  return (
    <div className="glass rounded-2xl shadow-panel">
      <header className="flex items-center justify-between gap-3 border-b border-white/8 px-4 py-3">
        <h2 className="flex items-center gap-2 font-display text-sm font-semibold tracking-tight text-text">
          <Wand2 size={15} className="text-accent" />
          Composed prompt
        </h2>
        {preview && (
          <Button
            variant="ghost"
            size="sm"
            onClick={copyPrompt}
            icon={copied ? <Check size={14} /> : <Copy size={14} />}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        )}
      </header>

      <div className="space-y-3 p-4">
        {loading && !preview && (
          <div className="flex items-center gap-2 text-sm text-text-muted">
            <Spinner size={16} />
            Composing the prompt
          </div>
        )}

        {error && !loading && (
          <div className="flex items-start gap-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-200 ring-1 ring-red-500/25">
            <AlertTriangle size={15} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {!loading && !error && !preview && (
          <p className="text-sm text-text-muted">
            Send a request or adjust the controls to compose a prompt.
          </p>
        )}

        {preview && (
          <>
            <div className="flex flex-wrap gap-1.5">
              <Pill>refs: {preview.reference_images.length}</Pill>
              <Pill>{preview.backend}</Pill>
              <Pill>
                <span className="tabular-nums">
                  {preview.width} x {preview.height}
                </span>
              </Pill>
              {preview.variant && <Pill>variant: {preview.variant}</Pill>}
              {preview.transparent && <Pill>transparent</Pill>}
              <Pill>autonomy: auto with review · TODO(backend)</Pill>
            </div>

            <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-white/8 bg-chrome-bg/80 p-3 font-mono text-xs leading-relaxed text-text">
              {preview.prompt}
            </pre>

            {preview.reference_images.length > 0 && (
              <div className="flex items-start gap-2 text-xs text-text-muted">
                <Layers size={14} className="mt-0.5 shrink-0 text-text-faint" />
                <span>
                  References: {preview.reference_images.join(", ")}
                </span>
              </div>
            )}

            {preview.missing_refs.length > 0 && (
              <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200 ring-1 ring-amber-500/25">
                <ImageOff size={14} className="mt-0.5 shrink-0" />
                <span>
                  Missing references: {preview.missing_refs.join(", ")}
                </span>
              </div>
            )}

            {preview.warnings.length > 0 && (
              <ul className="space-y-1">
                {preview.warnings.map((w, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2 text-xs text-amber-200/90"
                  >
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                    <span>{w}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      <footer className="flex items-center justify-between gap-3 border-t border-white/8 px-4 py-3">
        <label className="flex items-center gap-2 text-xs text-text-muted">
          Variations
          <select
            className="h-8 rounded-lg border border-white/8 bg-chrome-raised/80 px-2 text-sm tabular-nums text-text focus:outline-none focus:ring-2 focus:ring-accent/50 disabled:opacity-50"
            value={count}
            disabled={generating}
            onChange={(e) => onCountChange(Number(e.target.value))}
          >
            {[1, 2, 3, 4, 6, 8].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>

        <Button
          variant="primary"
          size="md"
          onClick={onGenerate}
          loading={generating}
          disabled={!preview || loading}
          icon={!generating ? <Wand2 size={16} /> : undefined}
        >
          {generating ? "Generating" : "Generate"}
        </Button>
      </footer>
    </div>
  );
}
