import { useMemo } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { ArrowRight, CheckCircle2, ImagePlus, X } from "lucide-react";
import { api, ApiError, isNeedsKeyError } from "../../../api/client";
import { GlassPanel } from "../../../components/GlassPanel";
import { Button } from "../../../components/Button";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { spring } from "../../../lib/motion";
import type { AgentIdentity, DirectorAgentRef } from "../../../lib/types";

interface CreatedAgentCardProps {
  /** The agent Iris just created (or already had) for the active brand. */
  agent: DirectorAgentRef;
  /** Dismiss the card and return to the plain conversation. */
  onClose: () => void;
}

/**
 * The compact created-agent card shown after Iris creates an on-brand agent
 * identity from the conversation (a DirectorReply with intent "agent"). It is the
 * agent-intent analog of the pack/single result surfaces: Iris narrates the
 * creation in the transcript, and this card is the structured handle on what she
 * made. It shows the agent's avatar (the gradient orb until one is generated),
 * its name and role, a primary "Generate avatar" that calls the existing avatar
 * route, and a link to the brand page where the Agents section lives.
 *
 * "Generate avatar" runs the same POST .../agents/{agent}/avatar the Agents panel
 * uses. While it runs the button shows a working state; on success it settles
 * into a done state and the avatar (now backed by a real file) swaps into the
 * orb. A backend-needs-a-key 422 or any other failure surfaces inline; the agent
 * itself already exists, so this is a follow-up the operator can retry.
 */
export function CreatedAgentCard({ agent, onClose }: CreatedAgentCardProps) {
  const queryClient = useQueryClient();

  const avatar = useMutation<AgentIdentity, unknown, void>({
    mutationFn: () => api.generateBrandAgentAvatar(agent.brand, agent.slug),
    onSuccess: () => {
      // The brand's agent roster now carries a real avatar; refresh the brand
      // page's Agents section (it reads the agent team under this key).
      void queryClient.invalidateQueries({
        queryKey: ["agent-team", agent.brand],
      });
    },
  });

  const created = avatar.data ?? null;
  const working = avatar.isPending;
  const done = Boolean(created);

  // The avatar URL is null until the route returns one; until then the orb
  // fallback carries the agent's initial.
  const avatarUrl = created?.avatar_url ?? null;

  // A human-readable line for the inline error banner. The needs-key 422 carries
  // its own backend detail; anything else is a plain failure to retry.
  const errorMessage = useMemo(() => {
    const err = avatar.error;
    if (!err) return null;
    if (isNeedsKeyError(err)) {
      const { backend, detail } = err.needsKey;
      return detail || `The ${backend} backend needs a key before I can draw it.`;
    }
    if (err instanceof ApiError) {
      return err.message || "I could not generate the avatar. Try again.";
    }
    return "I could not generate the avatar. Try again.";
  }, [avatar.error]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 8, filter: "blur(4px)" }}
      animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      transition={spring.card}
    >
      <GlassPanel ring glow className="overflow-hidden">
        <div className="flex items-start justify-between gap-3 px-5 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <AgentAvatar name={agent.name} src={avatarUrl} size={44} />
            <div className="min-w-0">
              <p className="truncate font-display text-base font-semibold tracking-tight text-text">
                {agent.name}
              </p>
              <p className="mt-0.5 truncate text-sm text-text-muted">
                {agent.role}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Dismiss"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-text-faint transition-colors hover:bg-chrome-hover hover:text-text"
          >
            <X size={15} />
          </button>
        </div>

        {errorMessage && (
          <div className="mx-5 mb-3 rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200">
            {errorMessage}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-white/8 px-5 py-3">
          {done ? (
            <Button
              variant="secondary"
              icon={<CheckCircle2 size={15} className="text-accent" />}
              disabled
            >
              Avatar generated
            </Button>
          ) : (
            <Button
              variant="primary"
              loading={working}
              disabled={working}
              onClick={() => avatar.mutate()}
              icon={!working ? <ImagePlus size={15} /> : undefined}
            >
              {working ? "Generating avatar" : "Generate avatar"}
            </Button>
          )}
          <Link to={`/brands/${encodeURIComponent(agent.brand)}`}>
            <Button variant="ghost" icon={<ArrowRight size={15} />}>
              View on its brand
            </Button>
          </Link>
        </div>
      </GlassPanel>
    </motion.div>
  );
}
