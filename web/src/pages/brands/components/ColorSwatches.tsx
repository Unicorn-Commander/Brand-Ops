import { useState } from "react";
import { Check, Copy } from "lucide-react";
import type { BrandColors } from "../../../lib/types";

/**
 * The brand's palette as labeled swatches. Each role (primary, accent,
 * background, foreground, muted) shows its color and the hex, and clicking a
 * swatch copies the hex. Roles the API leaves null render a clear "not set"
 * placeholder rather than an empty box.
 */

interface Role {
  key: keyof BrandColors;
  label: string;
}

const ROLES: Role[] = [
  { key: "primary", label: "Primary" },
  { key: "accent", label: "Accent" },
  { key: "background", label: "Background" },
  { key: "foreground", label: "Foreground" },
  { key: "muted", label: "Muted" },
];

/** A checkerboard so light or near-bg swatches stay visible on dark chrome. */
const CHECKER =
  "repeating-conic-gradient(#2a2a3d 0% 25%, #1b1b27 0% 50%) 50% / 12px 12px";

function Swatch({ label, value }: { label: string; value: string | null }) {
  const [copied, setCopied] = useState(false);

  if (!value) {
    return (
      <div className="flex flex-col gap-2">
        <div
          className="flex h-16 w-full items-center justify-center rounded-lg border border-dashed border-chrome-border text-xs text-text-faint"
          style={{ background: CHECKER }}
        >
          not set
        </div>
        <div className="space-y-0.5">
          <p className="text-xs font-medium text-text">{label}</p>
          <p className="font-mono text-xs text-text-faint">—</p>
        </div>
      </div>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
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
      title={`Copy ${value}`}
      className="group flex flex-col gap-2 text-left focus-visible:outline-none"
    >
      <div
        className="relative h-16 w-full overflow-hidden rounded-lg ring-1 ring-white/10 transition-transform group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-accent/60"
        style={{ background: CHECKER }}
      >
        <span className="absolute inset-0" style={{ backgroundColor: value }} />
        <span className="absolute right-1.5 top-1.5 rounded-md bg-black/40 p-1 text-white opacity-0 transition-opacity group-hover:opacity-100">
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </span>
      </div>
      <div className="space-y-0.5">
        <p className="text-xs font-medium text-text">{label}</p>
        <p className="font-mono text-xs uppercase text-text-muted">{value}</p>
      </div>
    </button>
  );
}

export function ColorSwatches({ colors }: { colors: BrandColors }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {ROLES.map((role) => (
        <Swatch key={role.key} label={role.label} value={colors[role.key]} />
      ))}
    </div>
  );
}
