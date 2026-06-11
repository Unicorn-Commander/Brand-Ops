import { useState, type FormEvent, type KeyboardEvent } from "react";
import { motion } from "framer-motion";
import { Send } from "lucide-react";
import { Button } from "../../../components/Button";
import { GlassPanel } from "../../../components/GlassPanel";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { spring, staggerContainer, staggerItem } from "../../../lib/motion";
import type { Agent } from "../../../lib/agents";
import { AgentTranscript } from "./AgentTranscript";
import type { Turn } from "../lib/transcript";

interface ConversationPanelProps {
  /** The agent in the room: Iris, or the active brand's steward. */
  agent: Agent;
  /** Iris's (or the steward's) opening line for an empty conversation. */
  opener: string;
  turns: Turn[];
  /** True while Iris is replying, or a run is in flight. */
  busy: boolean;
  /** Send a free-text message to the agent. */
  onSend: (text: string) => void;
  /** Things you would ask a brand director, to seed an empty conversation. */
  suggestions: string[];
}

/**
 * The conversation, which is the heart of the Studio. You talk to Iris (or, when
 * a brand is focused, that brand's steward); she plans the work, calls the
 * specialists, and streams results into the canvas. An empty conversation opens
 * with the agent introducing themselves in first person. Enter sends, Shift plus
 * Enter inserts a newline.
 */
export function ConversationPanel({
  agent,
  opener,
  turns,
  busy,
  onSend,
  suggestions,
}: ConversationPanelProps) {
  const [text, setText] = useState("");

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    onSend(trimmed);
    setText("");
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const firstName = agent.name.split(" ")[0];

  return (
    <GlassPanel flush className="flex min-h-0 flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
        {turns.length === 0 ? (
          <div className="mx-auto flex max-w-xl flex-col items-center gap-5 px-2 py-8 text-center">
            <AgentAvatar name={agent.name} src={agent.avatarUrl} size={72} />
            <div className="space-y-2">
              <p className="font-display text-lg font-semibold tracking-tight text-text">
                {agent.name}
                <span className="ml-2 align-middle text-xs font-normal text-text-faint">
                  {agent.role}
                </span>
              </p>
              <p className="mx-auto max-w-md text-sm leading-relaxed text-text-muted">
                {opener}
              </p>
            </div>
            {suggestions.length > 0 && (
              <motion.div
                variants={staggerContainer}
                initial="hidden"
                animate="show"
                className="flex w-full flex-col items-stretch gap-2"
              >
                {suggestions.map((s) => (
                  <motion.button
                    key={s}
                    variants={staggerItem}
                    type="button"
                    onClick={() => onSend(s)}
                    whileHover={{ y: -2 }}
                    transition={spring.snap}
                    className="glass rounded-xl px-3.5 py-2.5 text-left text-sm text-text-muted transition-colors hover:text-text"
                  >
                    {s}
                  </motion.button>
                ))}
              </motion.div>
            )}
          </div>
        ) : (
          <AgentTranscript turns={turns} />
        )}
      </div>

      <form onSubmit={onSubmit} className="border-t border-white/8 p-3">
        <div className="flex items-end gap-2 rounded-2xl border border-white/8 bg-chrome-raised/80 p-2 backdrop-blur transition-shadow focus-within:ring-2 focus-within:ring-accent/50">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder={`Ask ${firstName} for the launch kit, an OG image, or a brand check`}
            className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent px-1 py-1 text-sm text-text placeholder:text-text-faint focus:outline-none"
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={busy}
            disabled={!text.trim()}
            icon={!busy ? <Send size={14} /> : undefined}
            aria-label="Send message"
          >
            Send
          </Button>
        </div>
        <p className="mt-1.5 px-1 text-xs text-text-faint">
          Enter to send, Shift plus Enter for a new line.
        </p>
      </form>
    </GlassPanel>
  );
}
