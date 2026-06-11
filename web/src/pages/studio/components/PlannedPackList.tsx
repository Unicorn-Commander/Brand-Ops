import { useState } from "react";
import { motion } from "framer-motion";
import {
  Layers,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  AlertTriangle,
  Lock,
} from "lucide-react";
import { staggerContainer, staggerItem } from "../../../lib/motion";
import type { PlannedPack, PlannedItem } from "../lib/pack";
import { PACK_LABELS, type PackName } from "../lib/pack";

interface PlannedPackListProps {
  plan: PlannedPack;
}

/** Friendly label for a pack name, falling back to the raw key. */
function packLabel(pack: string): string {
  return PACK_LABELS[pack as PackName] ?? `${pack} pack`;
}

function StatusPill({ status }: { status: string }) {
  const ok = status === "planned" || status === "generated";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[0.7rem] font-medium ${
        ok
          ? "bg-accent/10 text-accent ring-1 ring-accent/25"
          : "bg-red-500/10 text-red-300 ring-1 ring-red-500/25"
      }`}
    >
      {status}
    </span>
  );
}

/** One planned asset: type + variant header over its composed prompt. */
function PlannedRow({ item, index }: { item: PlannedItem; index: number }) {
  // The first couple of prompts are expanded so the plan reads as "live"; the
  // rest collapse to keep a five-item pack scannable.
  const [open, setOpen] = useState(index < 2);
  const [copied, setCopied] = useState(false);
  const failed = item.status === "error";

  const copy = async () => {
    if (!item.prompt) return;
    try {
      await navigator.clipboard.writeText(item.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard can be blocked; ignore rather than disrupt the flow.
    }
  };

  return (
    <motion.li
      variants={staggerItem}
      className="overflow-hidden rounded-xl border border-white/8 bg-chrome-panel/80 backdrop-blur"
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
          disabled={!item.prompt && !item.error}
        >
          <span className="shrink-0 text-text-faint">
            {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </span>
          <span className="shrink-0 rounded-md bg-chrome-raised px-1.5 py-0.5 font-mono text-[0.7rem] tabular-nums text-text-muted">
            {index + 1}
          </span>
          <span className="truncate text-sm font-medium text-text">
            {item.asset_type}
          </span>
          {item.variant && (
            <span className="shrink-0 rounded-md border border-white/8 px-1.5 py-0.5 text-[0.7rem] text-text-muted">
              {item.variant}
            </span>
          )}
        </button>
        <StatusPill status={item.status} />
        {item.prompt && (
          <button
            type="button"
            onClick={copy}
            title={copied ? "Prompt copied" : "Copy prompt"}
            className="shrink-0 rounded-md p-1 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        )}
      </div>

      {open && (
        <div className="border-t border-white/8 px-3 py-2">
          {failed && item.error ? (
            <p className="flex items-start gap-2 text-xs text-red-200">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              <span>{item.error}</span>
            </p>
          ) : item.prompt ? (
            <pre className="max-h-44 overflow-auto whitespace-pre-wrap rounded-md border border-white/8 bg-chrome-bg/80 p-2.5 font-mono text-xs leading-relaxed text-text">
              {item.prompt}
            </pre>
          ) : (
            <p className="text-xs text-text-muted">No prompt was composed.</p>
          )}
        </div>
      )}
    </motion.li>
  );
}

/**
 * The planned-pack view in the canvas.
 *
 * Renders the agent's dry-run plan: a header (which pack, how many items, that
 * generation is held), then one row per planned asset showing its asset_type,
 * variant, and the composed prompt the engine would use. This is the "show the
 * plan and prompts live" surface, the pack analogue of the single-asset prompt
 * card. Generation stays gated, so there are no images here, only the plan.
 */
export function PlannedPackList({ plan }: PlannedPackListProps) {
  const ok = plan.items.filter((i) => i.status !== "error").length;
  const failed = plan.items.length - ok;

  return (
    <div className="flex flex-col gap-3">
      <div className="glass rounded-xl px-3 py-2.5">
        <div className="flex items-center gap-2">
          <Layers size={16} className="shrink-0 text-accent" />
          <span className="font-display text-sm font-semibold tracking-tight text-text">
            {packLabel(plan.pack)}
          </span>
          <span className="text-xs text-text-muted">
            <span className="tabular-nums">{plan.items.length}</span>{" "}
            {plan.items.length === 1 ? "asset" : "assets"}
            {failed > 0 ? ` (${failed} failed)` : ""}
          </span>
        </div>
        {plan.dryRun && (
          <p className="mt-1.5 flex items-center gap-1.5 text-xs text-text-muted">
            <Lock size={12} className="shrink-0 text-text-faint" />
            Planned only. Each asset shows the prompt the engine would use;
            generation is held until a backend key is set.
          </p>
        )}
      </div>

      <motion.ul
        variants={staggerContainer}
        initial="hidden"
        animate="show"
        className="space-y-2"
      >
        {plan.items.map((item, i) => (
          <PlannedRow
            key={`${item.asset_type}-${item.variant ?? "default"}-${i}`}
            item={item}
            index={i}
          />
        ))}
      </motion.ul>
    </div>
  );
}
