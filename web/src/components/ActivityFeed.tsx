/**
 * ActivityFeed: the provenance log surface.
 *
 * Renders GET /api/activity newest-first as a vertical timeline of who did what
 * across the suite: humans (the operator) and agents (Iris, asset packs, the
 * director) and the system (the compliance gate). Each row carries an actor
 * glyph, the action, the target (deep-linked to its brand), a relative
 * timestamp, and an optional note. It is the orientation surface when both
 * people and agents act on a brand, so the actor kind is always legible at a
 * glance.
 *
 * Reusable in two places: the Brands home (compact, cross-brand) and a brand
 * record (filtered to that brand). Pass `brand` to scope, `limit` to cap.
 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Shield, Cog, Activity as ActivityIcon } from "lucide-react";
import { listActivity } from "../api/client";
import { Spinner } from "./Spinner";
import { EmptyState } from "./EmptyState";
import { AgentAvatar } from "./AgentAvatar";
import { relativeTime } from "../lib/coverage";
import type { ActivityEvent } from "../lib/types";

interface ActivityFeedProps {
  /** Scope to one brand slug; omit for the cross-brand feed. */
  brand?: string;
  /** Cap the number of events shown. */
  limit?: number;
}

/**
 * Action tint buckets. We classify by keyword in the action verb rather than an
 * enum the server does not promise, so a new action phrase still lands in a
 * sensible bucket (and falls back to neutral). The accent maps to the lifecycle
 * palette: emerald = approved/cleared, amber = needs-review/flagged, red =
 * changes requested, muted = archived, accent = a creative/agent action.
 */
type Tint = {
  /** Left rail + glyph accent. */
  rail: string;
  /** The action verb's text color. */
  verb: string;
};

const NEUTRAL: Tint = { rail: "bg-white/10", verb: "text-text" };

function tintForAction(action: string): Tint {
  const a = action.toLowerCase();
  if (a.includes("approv") || a.includes("clear")) {
    return { rail: "bg-emerald-400/70", verb: "text-emerald-300" };
  }
  if (a.includes("request") || a.includes("changes")) {
    return { rail: "bg-red-400/70", verb: "text-red-300" };
  }
  if (a.includes("archiv")) {
    return { rail: "bg-white/15", verb: "text-text-muted" };
  }
  if (a.includes("flag") || a.includes("review") || a.includes("off-brand")) {
    return { rail: "bg-amber-400/70", verb: "text-amber-300" };
  }
  if (
    a.includes("generat") ||
    a.includes("ran") ||
    a.includes("pack") ||
    a.includes("upload")
  ) {
    return { rail: "bg-accent/70", verb: "text-accent" };
  }
  return NEUTRAL;
}

/**
 * The actor glyph. A human reads as a small initial dot; an agent reuses the
 * gradient AgentAvatar (so Iris and per-brand stewards look like themselves); a
 * system actor reads as a shield (compliance) or a gear (anything else).
 */
function ActorGlyph({ event }: { event: ActivityEvent }) {
  if (event.actor_kind === "agent") {
    return <AgentAvatar name={event.actor} size={26} ring={false} />;
  }
  if (event.actor_kind === "system") {
    const isCompliance = event.actor.toLowerCase().includes("compli");
    const Icon = isCompliance ? Shield : Cog;
    return (
      <span
        aria-label={event.actor}
        role="img"
        className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full bg-chrome-raised text-text-muted ring-1 ring-white/10"
      >
        <Icon size={13} aria-hidden />
      </span>
    );
  }
  // Human: an initial dot.
  const initial = event.actor.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      aria-label={event.actor}
      role="img"
      className="grid h-[26px] w-[26px] shrink-0 place-items-center rounded-full bg-white/[0.06] text-[11px] font-semibold text-text ring-1 ring-white/10"
    >
      {initial}
    </span>
  );
}

/** One event row: glyph, then "{actor} {action} {target}", time, and note. */
function EventRow({ event }: { event: ActivityEvent }) {
  const tint = tintForAction(event.action);
  return (
    <li className="relative flex gap-3 pb-3 last:pb-0">
      {/* Timeline rail behind the glyph (drawn full-height, the glyph masks it). */}
      <div className="relative flex flex-col items-center">
        <span
          aria-hidden
          className={`absolute top-0 bottom-0 w-px ${tint.rail} opacity-30`}
        />
        <span className="relative z-[1]">
          <ActorGlyph event={event} />
        </span>
      </div>

      <div className="min-w-0 flex-1 pt-0.5">
        <p className="text-sm leading-snug text-text-muted">
          <span className="font-medium text-text">{event.actor}</span>{" "}
          <span className={tint.verb}>{event.action}</span>
          {event.target && (
            <>
              {" "}
              {event.brand ? (
                <Link
                  to={`/brands/${encodeURIComponent(event.brand)}`}
                  className="font-mono text-xs text-text hover:text-accent"
                  title={`${event.target} · ${event.brand}`}
                >
                  {event.target}
                </Link>
              ) : (
                <span className="font-mono text-xs text-text" title={event.target}>
                  {event.target}
                </span>
              )}
            </>
          )}
        </p>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-text-faint">
          <span className="tabular-nums">{relativeTime(event.ts)}</span>
          {event.brand && (
            <>
              <span aria-hidden>·</span>
              <Link
                to={`/brands/${encodeURIComponent(event.brand)}`}
                className="hover:text-accent"
              >
                {event.brand}
              </Link>
            </>
          )}
        </div>
        {event.note && (
          <p className="mt-1 rounded-lg border border-white/8 bg-white/[0.02] px-2.5 py-1.5 text-xs leading-relaxed text-text-muted">
            {event.note}
          </p>
        )}
      </div>
    </li>
  );
}

/**
 * The feed. Loading and empty states use the shared Spinner/EmptyState. Errors
 * are tasteful and non-blocking: a feed that cannot load never disrupts the
 * page it sits on (it is a secondary, ambient surface).
 */
export function ActivityFeed({ brand, limit }: ActivityFeedProps) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["activity", brand ?? null, limit ?? null] as const,
    queryFn: ({ signal }) => listActivity({ brand, limit }, signal),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Spinner />
      </div>
    );
  }

  if (isError) {
    return (
      <EmptyState
        icon={<ActivityIcon size={22} />}
        title="Activity unavailable"
        description="The activity log did not load. It will appear once the server responds."
      />
    );
  }

  const events = data ?? [];
  if (events.length === 0) {
    return (
      <EmptyState
        icon={<ActivityIcon size={22} />}
        title="No activity yet"
        description={
          brand
            ? "Approvals, change requests, and agent runs for this brand will appear here."
            : "Approvals, change requests, uploads, and agent runs will appear here."
        }
      />
    );
  }

  return (
    <ul className="space-y-0">
      {events.map((event) => (
        <EventRow key={event.id} event={event} />
      ))}
    </ul>
  );
}
