import type { ReactNode } from "react";
import { motion } from "framer-motion";
import {
  Check,
  MessageSquareWarning,
  Archive,
  CheckCheck,
  X,
  Download,
} from "lucide-react";
import { Spinner } from "../../../components/Spinner";

interface BulkActionBarProps {
  /** How many in-view assets are selected. */
  count: number;
  /** Whether every asset currently in view is selected (drives All/Clear). */
  allSelected: boolean;
  /** A bulk transition is in flight; disables actions and shows a spinner. */
  busy: boolean;
  /** Human-readable progress line while a zip is being built, else null. */
  progress?: string | null;
  onDownload: () => void;
  onApprove: () => void;
  onRequestChanges: () => void;
  onArchive: () => void;
  onToggleAll: () => void;
  onCancel: () => void;
}

type Tone = "accent" | "emerald" | "amber" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  accent: "text-accent hover:bg-accent/10",
  emerald: "text-emerald-300 hover:bg-emerald-400/10 hover:text-emerald-200",
  amber: "text-amber-300 hover:bg-amber-400/10 hover:text-amber-200",
  muted: "text-text-muted hover:bg-white/5 hover:text-text",
};

function BarButton({
  onClick,
  disabled,
  icon,
  label,
  tone,
}: {
  onClick: () => void;
  disabled: boolean;
  icon: ReactNode;
  label: string;
  tone: Tone;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${TONE_CLASS[tone]}`}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

/**
 * The floating action bar for Library multi-select. Appears while selection
 * mode is on; its actions run the same lifecycle transitions the per-asset
 * review uses, in bulk, over every selected asset. All three transitions are
 * reversible (the gate moves records in both directions), so no destructive
 * confirm is needed. Pinned bottom-center, above the grid, pointer-events
 * scoped to the pill so the grid behind it stays clickable.
 */
export function BulkActionBar({
  count,
  allSelected,
  busy,
  progress = null,
  onDownload,
  onApprove,
  onRequestChanges,
  onArchive,
  onToggleAll,
  onCancel,
}: BulkActionBarProps) {
  const noneSelected = count === 0;
  return (
    <motion.div
      initial={{ y: 28, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      exit={{ y: 28, opacity: 0 }}
      transition={{ type: "spring", stiffness: 380, damping: 30 }}
      className="pointer-events-none fixed inset-x-0 bottom-5 z-40 flex justify-center px-4"
    >
      <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-white/10 bg-chrome-raised/95 px-2.5 py-1.5 shadow-glow backdrop-blur-xl">
        <span className="flex items-center gap-2 px-2.5 text-sm font-medium tabular-nums text-text">
          {busy ? (
            <>
              <Spinner size={14} /> {progress ?? "Working"}
            </>
          ) : (
            <>
              {count} <span className="text-text-muted">selected</span>
            </>
          )}
        </span>
        <span className="h-5 w-px bg-white/10" />
        <BarButton
          onClick={onDownload}
          disabled={busy || noneSelected}
          icon={<Download size={15} />}
          label="Download"
          tone="accent"
        />
        <span className="h-5 w-px bg-white/10" />
        <BarButton
          onClick={onApprove}
          disabled={busy || noneSelected}
          icon={<Check size={15} />}
          label="Approve"
          tone="emerald"
        />
        <BarButton
          onClick={onRequestChanges}
          disabled={busy || noneSelected}
          icon={<MessageSquareWarning size={15} />}
          label="Request changes"
          tone="amber"
        />
        <BarButton
          onClick={onArchive}
          disabled={busy || noneSelected}
          icon={<Archive size={15} />}
          label="Archive"
          tone="muted"
        />
        <span className="h-5 w-px bg-white/10" />
        <BarButton
          onClick={onToggleAll}
          disabled={busy}
          icon={<CheckCheck size={15} />}
          label={allSelected ? "Clear" : "Select all"}
          tone="muted"
        />
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          aria-label="Exit selection"
          className="ml-0.5 grid h-8 w-8 place-items-center rounded-full text-text-muted transition-colors hover:bg-white/5 hover:text-text disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          <X size={16} />
        </button>
      </div>
    </motion.div>
  );
}
