/**
 * Conversation transcript model for the Studio screen.
 *
 * The transcript is an ordered list of turns shown in the left panel. A turn is
 * either something the user said, or a step the (stubbed) agent took: planning,
 * a composed prompt that is ready, a generation run, or a plain status note.
 * Keeping this as data (not JSX) lets the screen drive the transcript from its
 * state machine and lets a future real agent append the same turn shapes.
 */

export type TurnRole = "user" | "agent";

/**
 * The kind of agent turn, which selects an icon and tone in the renderer.
 * `plan` = thinking it through, `pack` = a planned pack of assets is ready,
 * `prompt` = a single composed prompt is ready to preview, `generating` = a live
 * run is in flight, `result` = a run finished, `note` = an informational or
 * error line.
 */
export type AgentKind =
  | "plan"
  | "pack"
  | "prompt"
  | "generating"
  | "result"
  | "note";

export interface UserTurn {
  id: string;
  role: "user";
  text: string;
}

/**
 * Who is speaking an agent turn, for the transcript to render their avatar and
 * name. Iris (the director) or a brand steward; carried on each agent turn so a
 * conversation can switch speakers as the active brand changes.
 */
export interface TurnSpeaker {
  name: string;
  /** A mark/photo URL when the speaker has one (a steward's brand mark); null for Iris. */
  avatarUrl: string | null;
}

export interface AgentTurn {
  id: string;
  role: "agent";
  kind: AgentKind;
  text: string;
  /** Optional sub-lines, e.g. the agent's per-field rationale. */
  details?: string[];
  /** Marks an error note so the renderer can tint it. */
  error?: boolean;
  /** Who said it: Iris or the active brand's steward. */
  speaker?: TurnSpeaker;
}

export type Turn = UserTurn | AgentTurn;

let seq = 0;
/** Monotonic id so React keys are stable across re-renders within a session. */
function nextId(): string {
  seq += 1;
  return `t${seq}`;
}

export function userTurn(text: string): UserTurn {
  return { id: nextId(), role: "user", text };
}

export function agentTurn(
  kind: AgentKind,
  text: string,
  extra?: { details?: string[]; error?: boolean; speaker?: TurnSpeaker },
): AgentTurn {
  return { id: nextId(), role: "agent", kind, text, ...extra };
}
