import { useState, type FormEvent, type KeyboardEvent } from "react";
import { Send, Sparkles } from "lucide-react";
import { Button } from "../../../components/Button";
import { EmptyState } from "../../../components/EmptyState";
import { AgentTranscript } from "./AgentTranscript";
import type { Turn } from "../lib/transcript";

interface ConversationPanelProps {
  turns: Turn[];
  /** True while the stub is composing a prompt or a run is in flight. */
  busy: boolean;
  /** Send a free-text request to the agent stub. */
  onSend: (text: string) => void;
  /** Example prompts to seed an empty conversation. */
  suggestions: string[];
}

/**
 * The left panel: a chat-style input over a scrolling transcript. The user
 * describes what they want; the transcript shows the stub's planning and the
 * composed prompt. Enter sends, Shift+Enter inserts a newline.
 */
export function ConversationPanel({
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

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {turns.length === 0 ? (
          <EmptyState
            icon={<Sparkles size={26} />}
            title="Describe what you want"
            description="Tell the agent the asset, the brand, and any direction. It composes a prompt you can review before generating."
            action={
              suggestions.length > 0 ? (
                <div className="flex flex-col items-stretch gap-2">
                  {suggestions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => onSend(s)}
                      className="rounded-lg border border-chrome-border bg-chrome-raised px-3 py-2 text-left text-xs text-text-muted transition-colors hover:bg-chrome-hover hover:text-text"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              ) : undefined
            }
          />
        ) : (
          <AgentTranscript turns={turns} />
        )}
      </div>

      <form
        onSubmit={onSubmit}
        className="border-t border-chrome-border p-3"
      >
        <div className="flex items-end gap-2 rounded-xl border border-chrome-border bg-chrome-raised p-2 focus-within:ring-2 focus-within:ring-accent/50">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            placeholder="Make an OG image for the investor update, dark"
            className="max-h-40 min-h-[2.5rem] flex-1 resize-none bg-transparent px-1 py-1 text-sm text-text placeholder:text-text-faint focus:outline-none"
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            loading={busy}
            disabled={!text.trim()}
            icon={!busy ? <Send size={14} /> : undefined}
            aria-label="Send request"
          >
            Send
          </Button>
        </div>
        <p className="mt-1.5 px-1 text-xs text-text-faint">
          Enter to send, Shift plus Enter for a new line.
        </p>
      </form>
    </div>
  );
}
