import { useEffect, useRef } from "react";
import {
  Brain,
  FileText,
  Layers,
  Loader2,
  CheckCircle2,
  Info,
  AlertTriangle,
  User,
} from "lucide-react";
import type { AgentKind, Turn } from "../lib/transcript";

/** Icon + accessible label per agent turn kind. */
function agentIcon(kind: AgentKind, error: boolean) {
  if (error) return <AlertTriangle size={15} />;
  switch (kind) {
    case "plan":
      return <Brain size={15} />;
    case "pack":
      return <Layers size={15} />;
    case "prompt":
      return <FileText size={15} />;
    case "generating":
      return <Loader2 size={15} className="animate-spin" />;
    case "result":
      return <CheckCircle2 size={15} />;
    case "note":
    default:
      return <Info size={15} />;
  }
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] items-start gap-2">
        <div className="rounded-2xl rounded-tr-sm bg-accent/15 px-3 py-2 text-sm text-text ring-1 ring-accent/25">
          {text}
        </div>
        <span className="mt-1.5 shrink-0 text-text-faint">
          <User size={15} />
        </span>
      </div>
    </div>
  );
}

function AgentBubble({
  kind,
  text,
  details,
  error,
}: {
  kind: AgentKind;
  text: string;
  details?: string[];
  error?: boolean;
}) {
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[90%] items-start gap-2">
        <span
          className={`mt-1.5 shrink-0 ${error ? "text-red-400" : "text-accent"}`}
        >
          {agentIcon(kind, Boolean(error))}
        </span>
        <div
          className={`rounded-2xl rounded-tl-sm px-3 py-2 text-sm ${
            error
              ? "bg-red-500/10 text-red-200 ring-1 ring-red-500/25"
              : "bg-chrome-raised text-text ring-1 ring-chrome-border"
          }`}
        >
          <p className="leading-snug">{text}</p>
          {details && details.length > 0 && (
            <ul className="mt-2 space-y-1 border-t border-chrome-border/60 pt-2 text-xs text-text-muted">
              {details.map((d, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-text-faint">-</span>
                  <span>{d}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Renders the ordered conversation: user turns on the right, agent steps on the
 * left. Auto-scrolls to the newest turn as the transcript grows.
 */
export function AgentTranscript({ turns }: { turns: Turn[] }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="flex flex-col gap-3">
      {turns.map((turn) =>
        turn.role === "user" ? (
          <UserBubble key={turn.id} text={turn.text} />
        ) : (
          <AgentBubble
            key={turn.id}
            kind={turn.kind}
            text={turn.text}
            details={turn.details}
            error={turn.error}
          />
        ),
      )}
      <div ref={endRef} />
    </div>
  );
}
