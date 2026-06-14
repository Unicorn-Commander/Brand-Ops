/**
 * Pack intent + result narrowing for the agent-first Studio.
 *
 * Two jobs, both pure and synchronous:
 *
 *  1. inferPack(): decide whether a free-text request is asking for a *set* of
 *     assets (a pack) rather than a single asset, and if so which named pack to
 *     run. The five packs the AssetPackAgent ships are core, social, logo,
 *     podcast, and launch; an explicit pack name in the request wins, then a
 *     topical hint (a launch/social/podcast/logo phrasing), then a bare
 *     "a set / a kit / a pack / everything" falls back to `launch`.
 *
 *  2. readPlannedItems(): narrow the agent's untyped `result` (the run endpoint
 *     passes the agent's dict through verbatim as Record<string, unknown>) into
 *     a typed list of planned items the canvas can render, defensively, so a
 *     shape drift never throws in the UI.
 *
 * No network and no LLM here: this only classifies text and reshapes a result.
 * The actual planning happens server-side in AssetPackAgent.run via dry_run.
 */
import type { AgentRunResult } from "../../../lib/types";

/** The named packs the AssetPackAgent exposes. Source: agents/asset_pack.py. */
export const KNOWN_PACKS = [
  "core",
  "social",
  "logo",
  "podcast",
  "launch",
] as const;

export type PackName = (typeof KNOWN_PACKS)[number];

/** The pack run when the request asks for a set without naming one. */
export const DEFAULT_PACK: PackName = "launch";

/** A short, human label per pack for the transcript and canvas header. */
export const PACK_LABELS: Record<PackName, string> = {
  core: "Core kit",
  social: "Social set",
  logo: "Logo set",
  podcast: "Podcast set",
  launch: "Launch kit",
};

/**
 * Phrases that signal the user wants a *whole set*, not one asset. Kept separate
 * from the pack-name hints so "make a logo" (single) stays distinct from "make
 * the logo set" (pack): a set word must be present for those overlapping cases.
 */
const SET_WORDS = [
  "pack",
  "kit",
  "set",
  "bundle",
  "suite",
  "everything",
  "all the assets",
  "all assets",
  "full set",
  "whole set",
  "a bunch of",
  "multiple",
];

/**
 * Per-pack hint phrases, in priority order. A match here selects that pack, but
 * only when the request also reads as a set (see resolvePack), except for the
 * unambiguous multi-asset packs (launch / social / core) whose own words already
 * imply a set.
 */
const PACK_HINTS: { pack: PackName; phrases: string[]; impliesSet: boolean }[] = [
  {
    pack: "launch",
    phrases: ["launch kit", "launch pack", "launch set", "launch bundle", "go to market", "go-to-market", "ship it", "for launch", "launch assets"],
    impliesSet: true,
  },
  {
    pack: "social",
    phrases: ["social pack", "social set", "social kit", "social bundle", "social media assets", "social cards", "all the social"],
    impliesSet: true,
  },
  {
    pack: "core",
    phrases: ["core kit", "core pack", "core set", "brand kit", "starter kit", "essentials", "the basics", "core assets"],
    impliesSet: true,
  },
  {
    pack: "podcast",
    phrases: ["podcast pack", "podcast set", "podcast kit", "podcast bundle", "show art set", "podcast assets"],
    impliesSet: false,
  },
  {
    pack: "logo",
    phrases: ["logo pack", "logo set", "logo kit", "logo variations", "logo variants", "all the logos", "logo suite"],
    impliesSet: false,
  },
];

/** The outcome of classifying a request as a pack request. */
export interface PackIntent {
  /** The pack to run. */
  pack: PackName;
  /**
   * How the pack was chosen, in plain language, for the transcript. e.g.
   * `Named the social pack.` or `Asked for a set; defaulting to the launch kit.`
   */
  reason: string;
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

/** True when the request reads as wanting a whole set rather than one asset. */
function mentionsSet(text: string): boolean {
  return SET_WORDS.some((w) => text.includes(w));
}

/** A bare pack name said directly, e.g. "run the launch pack" -> launch. */
function namedPack(text: string): PackName | null {
  for (const pack of KNOWN_PACKS) {
    // Require a set word adjacent to a bare pack name like "core"/"social" so a
    // single "make a logo" never trips the "logo" pack; the *_HINTS below
    // already cover the explicit "logo set" phrasings.
    if (text.includes(`${pack} pack`) || text.includes(`${pack} kit`) || text.includes(`${pack} set`)) {
      return pack;
    }
  }
  return null;
}

/**
 * Verbs/phrases of INSPECTION: the operator wants existing work judged against
 * the brand, not new work made. The deterministic chat backstop must never turn
 * one of these into a pack plan (the check-vs-make bug), so inferPack bails on
 * them. The Director's "compliance" intent is the real home for these; this is
 * only the local backstop's safety net when the model under-classifies.
 */
const INSPECTION_WORDS = [
  "check",
  "audit",
  "review",
  "score",
  "validate",
  "vet",
  "compliance",
  "on brand",
  "on-brand",
  "off brand",
  "off-brand",
  "does this pass",
  "is this ok",
];

/**
 * Decide whether a request is a pack request, and which pack. Returns null for a
 * single-asset request (the caller then falls back to a one-asset preview).
 *
 * Order: an inspection request bails immediately (it is a check, not a make);
 * then an explicit "<pack> pack/kit/set" wins; then a pack hint phrase (gated by
 * a set word unless the pack's words already imply a set); then a bare set word
 * with no specific pack falls back to DEFAULT_PACK.
 */
export function inferPack(rawText: string): PackIntent | null {
  const text = normalize(rawText);

  // A check is not a make: never plan a pack for an inspection ask.
  if (INSPECTION_WORDS.some((w) => text.includes(w))) return null;

  const named = namedPack(text);
  if (named) {
    return { pack: named, reason: `Named the ${PACK_LABELS[named].toLowerCase()}.` };
  }

  const isSet = mentionsSet(text);
  for (const hint of PACK_HINTS) {
    if (!hint.phrases.some((p) => text.includes(p))) continue;
    if (hint.impliesSet || isSet) {
      return {
        pack: hint.pack,
        reason: `Matched the ${PACK_LABELS[hint.pack].toLowerCase()}.`,
      };
    }
  }

  if (isSet) {
    return {
      pack: DEFAULT_PACK,
      reason: `Asked for a set; planning the ${PACK_LABELS[DEFAULT_PACK].toLowerCase()}.`,
    };
  }

  return null;
}

/** One planned asset lifted from the agent result, typed for the canvas. */
export interface PlannedItem {
  asset_type: string;
  variant: string | null;
  /** The composed prompt, when the engine built one (dry-run plans carry it). */
  prompt: string | null;
  /** Where the asset would be written; informational only in a dry run. */
  planned_path: string | null;
  /** Per-item status the agent stamped: "planned", "generated", or "error". */
  status: string;
  /** Present only when this item failed to plan; surfaced inline. */
  error: string | null;
}

/** The full planned pack, narrowed from an AgentRunResult. */
export interface PlannedPack {
  brand: string;
  pack: string;
  dryRun: boolean;
  items: PlannedItem[];
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asItem(value: unknown): PlannedItem | null {
  if (typeof value !== "object" || value === null) return null;
  const o = value as Record<string, unknown>;
  const assetType = asString(o.asset_type);
  if (!assetType) return null;
  return {
    asset_type: assetType,
    variant: asString(o.variant),
    prompt: asString(o.prompt),
    planned_path: asString(o.planned_path),
    status: asString(o.status) ?? "planned",
    error: asString(o.error),
  };
}

/**
 * Narrow an AgentRunResult from a dry-run asset_pack call into a typed
 * PlannedPack. Returns null when the result is not a pack shape (e.g. the agent
 * returned an `error` status with no items), letting the caller report the
 * agent's own error message instead.
 *
 * `fallbackBrand`/`fallbackPack` fill the header when the agent omitted them
 * (it echoes both on success, so this is belt-and-braces).
 */
export function readPlannedItems(
  run: AgentRunResult,
  fallbackBrand: string,
  fallbackPack: string,
): PlannedPack | null {
  const result = run.result ?? {};
  const rawItems = (result as Record<string, unknown>).items;
  if (!Array.isArray(rawItems)) return null;

  const items = rawItems
    .map(asItem)
    .filter((it): it is PlannedItem => it !== null);
  if (items.length === 0) return null;

  return {
    brand: asString((result as Record<string, unknown>).brand) ?? fallbackBrand,
    pack: asString((result as Record<string, unknown>).pack) ?? fallbackPack,
    dryRun: (result as Record<string, unknown>).dry_run !== false,
    items,
  };
}

/**
 * Pull a short error message out of an agent result that came back with a
 * non-ok status (e.g. `{status:"error", error:"unknown pack ..."}`). Falls back
 * to a generic line so the transcript always has something to show.
 */
export function packErrorMessage(run: AgentRunResult): string {
  const result = (run.result ?? {}) as Record<string, unknown>;
  return (
    asString(result.error) ??
    `The pack planner returned "${run.status}" with no items.`
  );
}
