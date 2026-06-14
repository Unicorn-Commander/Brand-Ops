import { useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import type { AssetType, BrandSummary } from "../../../lib/types";
import { useViewMode } from "../../../view-mode";
import { FIELD_LABELS, pick } from "../../../lib/labels";
import { easeExpo } from "../../../lib/motion";
import { OverrideField } from "../../../components/OverrideField";

/** The control values the screen tracks and feeds back into the agent stub. */
export interface ControlsState {
  brand: string | null;
  asset: string | null;
  variant: string | null;
  topic: string | null;
  backend: string | null;
}

interface ControlsStripProps {
  brands: BrandSummary[];
  assetTypes: AssetType[];
  /** Variants the active brand defines, surfaced as quick options. */
  variantOptions: string[];
  /** Backends seen across asset types, for the backend override list. */
  backendOptions: string[];
  value: ControlsState;
  onChange: (next: ControlsState) => void;
  disabled?: boolean;
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-text-muted">{label}</span>
      {children}
    </label>
  );
}

const SELECT_CLASS =
  "h-9 w-full min-w-0 rounded-lg border border-white/8 bg-chrome-raised/80 px-2.5 text-sm text-text " +
  "transition-colors hover:bg-chrome-hover focus:outline-none focus:ring-2 focus:ring-accent/50 " +
  "disabled:cursor-not-allowed disabled:opacity-50";

const INPUT_CLASS =
  "h-9 w-full min-w-0 rounded-lg border border-white/8 bg-chrome-raised/80 px-2.5 text-sm text-text " +
  "placeholder:text-text-faint transition-colors hover:bg-chrome-hover focus:outline-none focus:ring-2 " +
  "focus:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50";

/**
 * The controls strip beneath the conversation, built for progressive
 * disclosure so it serves a newcomer and a power user in ONE panel (never two
 * modes).
 *
 * PRIMARY (always visible): Brand, Asset, Topic. With sensible defaults these
 * alone are enough to Generate. The dense/power controls (Variant and especially
 * Backend/model) sit behind a "More options" disclosure: collapsed in Simple,
 * expanded in Advanced. Backend uses the Override pattern (Auto recommended until
 * you opt in). Labels come from FIELD_LABELS so they read plainly in Simple and
 * technically in Advanced. Nothing is hidden in either mode: Simple just
 * collapses and relabels; everything is reachable by expanding.
 */
export function ControlsStrip({
  brands,
  assetTypes,
  variantOptions,
  backendOptions,
  value,
  onChange,
  disabled = false,
}: ControlsStripProps) {
  const { mode, isAdvanced } = useViewMode();
  const reduce = useReducedMotion();

  // The power controls (Variant + Backend) open by default for power users and
  // collapse for newcomers. Local to this strip; the outer Fine-tune panel is
  // governed separately. Seeded from the mode and then user-controlled.
  const [moreOpen, setMoreOpen] = useState(isAdvanced);

  // Whether the operator has opted to override the backend (the Auto default).
  // A backend value implies an explicit choice, so start opened in that case.
  const [backendOverridden, setBackendOverridden] = useState(
    value.backend !== null,
  );

  const set = <K extends keyof ControlsState>(key: K, v: ControlsState[K]) =>
    onChange({ ...value, [key]: v });

  /** Empty string in a select maps back to null (Auto / default). */
  const toNull = (v: string): string | null => (v === "" ? null : v);

  // What "Auto" resolves to for the backend: the selected asset type's default,
  // falling back to the engine default (openrouter). This is the value the
  // OverrideField shows beside "Auto (recommended)" so the choice is legible
  // without overriding.
  const selectedAsset = assetTypes.find((a) => a.key === value.asset);
  const resolvedBackend = selectedAsset?.default_backend || "openrouter";

  // When the operator clears the override, drop any explicit backend so the
  // request falls back to the resolved default again.
  const handleBackendOverride = (next: boolean) => {
    setBackendOverridden(next);
    if (!next && value.backend !== null) set("backend", null);
  };

  return (
    <div className="rounded-xl border border-white/8 bg-chrome-raised/40 p-3">
      {/* PRIMARY: the essentials. Brand + Asset are all Generate needs; Topic is
          the one free-text refinement worth keeping in reach. */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Field label={pick(FIELD_LABELS.brand, mode)} htmlFor="ctl-brand">
          <select
            id="ctl-brand"
            className={SELECT_CLASS}
            value={value.brand ?? ""}
            disabled={disabled || brands.length === 0}
            onChange={(e) => set("brand", toNull(e.target.value))}
          >
            {brands.length === 0 && <option value="">No brands</option>}
            {brands.map((b) => (
              <option key={b.slug} value={b.slug}>
                {b.display_name}
              </option>
            ))}
          </select>
        </Field>

        <Field label={pick(FIELD_LABELS.asset, mode)} htmlFor="ctl-asset">
          <select
            id="ctl-asset"
            className={SELECT_CLASS}
            value={value.asset ?? ""}
            disabled={disabled || assetTypes.length === 0}
            onChange={(e) => set("asset", toNull(e.target.value))}
          >
            {assetTypes.length === 0 && <option value="">No asset types</option>}
            {assetTypes.map((a) => (
              <option key={a.key} value={a.key}>
                {a.label}
              </option>
            ))}
          </select>
        </Field>

        <Field label={pick(FIELD_LABELS.topic, mode)} htmlFor="ctl-topic">
          <input
            id="ctl-topic"
            type="text"
            className={INPUT_CLASS}
            placeholder="optional"
            value={value.topic ?? ""}
            disabled={disabled}
            onChange={(e) => set("topic", toNull(e.target.value))}
          />
        </Field>
      </div>

      {/* Power controls behind information scent. "More options" (not "Advanced
          options"): a clear, low-stakes invitation. Collapsed in Simple,
          expanded in Advanced. */}
      <div className="mt-3 border-t border-white/8 pt-3">
        <button
          type="button"
          onClick={() => setMoreOpen((v) => !v)}
          aria-expanded={moreOpen}
          aria-controls="ctl-more"
          className="flex items-center gap-1.5 text-xs font-medium text-text-muted transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 rounded-md"
        >
          <ChevronDown
            size={14}
            className={`shrink-0 text-text-faint transition-transform duration-200 ${
              moreOpen ? "rotate-180" : ""
            }`}
          />
          More options
        </button>

        <AnimatePresence initial={false}>
          {moreOpen && (
            <motion.div
              id="ctl-more"
              initial={reduce ? false : { height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={easeExpo}
              className="overflow-hidden"
            >
              <div className="grid grid-cols-1 gap-3 pt-3 sm:grid-cols-2">
                <Field
                  label={pick(FIELD_LABELS.variant, mode)}
                  htmlFor="ctl-variant"
                >
                  <select
                    id="ctl-variant"
                    className={SELECT_CLASS}
                    value={value.variant ?? ""}
                    disabled={disabled}
                    onChange={(e) => set("variant", toNull(e.target.value))}
                  >
                    <option value="">Auto</option>
                    {variantOptions.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </Field>

                {/* Backend/model: the densest field. Defaults to the
                    recommendation; the real select only appears on Override. In
                    Advanced we also surface the raw resolved id so pros see what
                    Auto picked. */}
                <OverrideField
                  label={pick(FIELD_LABELS.backend, mode)}
                  resolvedValue={resolvedBackend}
                  identifier={isAdvanced ? resolvedBackend : null}
                  overridden={backendOverridden}
                  onOverriddenChange={handleBackendOverride}
                  disabled={disabled}
                >
                  <select
                    id="ctl-backend"
                    aria-label={pick(FIELD_LABELS.backend, mode)}
                    className={SELECT_CLASS}
                    value={value.backend ?? ""}
                    disabled={disabled}
                    onChange={(e) => set("backend", toNull(e.target.value))}
                  >
                    <option value="">Default ({resolvedBackend})</option>
                    {backendOptions.map((b) => (
                      <option key={b} value={b}>
                        {b}
                      </option>
                    ))}
                  </select>
                </OverrideField>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
