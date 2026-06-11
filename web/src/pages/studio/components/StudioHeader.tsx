import { motion } from "framer-motion";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { spring } from "../../../lib/motion";
import type { Agent } from "../../../lib/agents";

interface StudioHeaderProps {
  /** The agent in the room: Iris (the director) or a brand steward. */
  agent: Agent;
  /**
   * A quiet line under the name: "Knows your 5 brands" for Iris, or
   * "Looking after <Brand>" for a steward. Already resolved by the page.
   */
  context: string;
}

/**
 * The Studio header: who you are working with. Iris when no brand is focused, a
 * brand steward when one is. The avatar and name make the surface read as a
 * person, not a tool; the context line says what they know.
 */
export function StudioHeader({ agent, context }: StudioHeaderProps) {
  return (
    <motion.div
      key={agent.id}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={spring.card}
      className="flex items-center gap-4"
    >
      <AgentAvatar name={agent.name} src={agent.avatarUrl} size={56} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
          <h1 className="font-display text-2xl font-semibold tracking-tight text-text">
            {agent.name}
          </h1>
          <span className="text-xs font-medium uppercase tracking-wider text-accent">
            {agent.role}
          </span>
        </div>
        <p className="mt-0.5 text-sm text-text-muted">{context}</p>
      </div>
    </motion.div>
  );
}
