import { type ReactNode } from "react";
import type { AssetType, BrandSummary } from "../../../lib/types";

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
 * The controls strip beneath the conversation. Each control refines the request;
 * the screen re-runs the prompt preview whenever a value changes. Brand and
 * asset come from the API; variant offers the brand's own variants plus a free
 * "Auto" (let the agent decide); topic is free text; backend overrides the asset
 * type's default.
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
  const set = <K extends keyof ControlsState>(key: K, v: ControlsState[K]) =>
    onChange({ ...value, [key]: v });

  /** Empty string in a select maps back to null (Auto / default). */
  const toNull = (v: string): string | null => (v === "" ? null : v);

  return (
    <div className="rounded-xl border border-white/8 bg-chrome-raised/40 p-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <Field label="Brand" htmlFor="ctl-brand">
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

        <Field label="Asset" htmlFor="ctl-asset">
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

        <Field label="Variant" htmlFor="ctl-variant">
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

        <Field label="Topic" htmlFor="ctl-topic">
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

        <Field label="Backend" htmlFor="ctl-backend">
          <select
            id="ctl-backend"
            className={SELECT_CLASS}
            value={value.backend ?? ""}
            disabled={disabled}
            onChange={(e) => set("backend", toNull(e.target.value))}
          >
            <option value="">Default</option>
            {backendOptions.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </div>
  );
}
