/**
 * The Studio agent identity model.
 *
 * Studio is a conversation with a named brand person, not a chat box over an
 * LLM. There are two roles:
 *
 *  - Iris, the brand DIRECTOR. The single agent you talk to across all brands.
 *    She orchestrates the specialists (research, asset_pack, the critic, the
 *    compliance gate) and speaks in first person.
 *  - A per-brand STEWARD. When you focus one brand you are working with that
 *    brand's steward, embodied by the brand's canonical mark and named for the
 *    brand. For now a Steward is derived from the brand record; real per-brand
 *    agent instances and Brigade-backed memory come later.
 *
 * `activeAgent` resolves which of the two is in the room: the Steward when a
 * brand is active, otherwise the Director.
 */
import type { BrandSummary } from "./types";

/** The identity a Studio surface renders: a name, a role, an optional mark, a quiet line. */
export interface Agent {
  /** Stable id where one is meaningful (the Director); Stewards use the brand slug. */
  id: string;
  /** Display name. Iris, or the brand's display name for a Steward. */
  name: string;
  /** Human role label shown under the name. */
  role: string;
  /** A mark/photo URL when the agent has one (a Steward's brand mark); null for the Director. */
  avatarUrl: string | null;
  /** A short, plain line of what they do. Optional; the Director carries one. */
  tagline?: string;
}

/**
 * Iris, the cross-brand director. The one agent you talk to in Studio. She is
 * the orchestrator, not a generic assistant.
 */
export const Director = {
  id: "iris",
  name: "Iris",
  role: "brand director",
  tagline: "Looks after your brands and makes the assets you need.",
} as const;

/**
 * Derive a brand's Steward from its summary record. The Steward is embodied by
 * the brand mark and named for the brand, with the fixed "brand steward" role.
 */
export function deriveSteward(brand: BrandSummary): Agent {
  return {
    id: brand.slug,
    name: brand.display_name,
    role: "brand steward",
    avatarUrl: brand.mark_url,
  };
}

/**
 * The agent currently in the room: the brand's Steward when a brand is active,
 * otherwise Iris the Director. Pass the active brand summary (or null).
 */
export function activeAgent(activeBrand: BrandSummary | null): Agent {
  if (activeBrand) return deriveSteward(activeBrand);
  return {
    id: Director.id,
    name: Director.name,
    role: Director.role,
    avatarUrl: null,
    tagline: Director.tagline,
  };
}
