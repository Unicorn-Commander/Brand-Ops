import { useEffect, useRef } from "react";
import { motion, useReducedMotion } from "framer-motion";
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
import { spring } from "../../../lib/motion";
import { AgentAvatar } from "../../../components/AgentAvatar";
import type { AgentKind, Turn, TurnSpeaker } from "../lib/transcript";

/** Small status glyph shown beside the agent's name, hinting what the turn is. */
function kindGlyph(kind: AgentKind, error: boolean) {
  if (error) return <AlertTriangle size={12} />;
  switch (kind) {
    case "plan":
      return <Brain size={12} />;
    case "pack":
      return <Layers size={12} />;
    case "prompt":
      return <FileText size={12} />;
    case "generating":
      return <Loader2 size={12} className="animate-spin" />;
    case "result":
      return <CheckCircle2 size={12} />;
    case "note":
    default:
      return <Info size={12} />;
  }
}

/** A short label per turn kind, for the line beside the name. */
function kindLabel(kind: AgentKind, error: boolean): string {
  if (error) return "flag";
  switch (kind) {
    case "plan":
      return "thinking";
    case "pack":
      return "pack";
    case "prompt":
      return "prompt";
    case "generating":
      return "working";
    case "result":
      return "done";
    case "note":
    default:
      return "note";
  }
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[82%] items-start gap-2.5">
        <div className="rounded-2xl rounded-tr-sm bg-accent/15 px-3.5 py-2.5 text-sm leading-relaxed text-text shadow-glow ring-1 ring-accent/30">
          {text}
        </div>
        <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-chrome-raised text-text-faint ring-1 ring-white/10">
          <User size={14} />
        </span>
      </div>
    </div>
  );
}

/** The agent (Iris or a steward) speaking: avatar + name header over the line. */
function AgentBubble({
  kind,
  text,
  details,
  error,
  speaker,
}: {
  kind: AgentKind;
  text: string;
  details?: string[];
  error?: boolean;
  speaker?: TurnSpeaker;
}) {
  const name = speaker?.name ?? "Iris";
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[88%] items-start gap-2.5">
        <AgentAvatar
          name={name}
          src={speaker?.avatarUrl}
          size={30}
          ring={false}
        />
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-1.5 px-0.5">
            <span className="text-xs font-semibold tracking-tight text-text">
              {name}
            </span>
            <span
              className={`inline-flex items-center gap-1 text-[0.7rem] ${
                error ? "text-red-400" : "text-text-faint"
              }`}
            >
              {kindGlyph(kind, Boolean(error))}
              {kindLabel(kind, Boolean(error))}
            </span>
          </div>
          <div
            className={`rounded-2xl rounded-tl-sm px-3.5 py-2.5 text-sm ${
              error
                ? "bg-red-500/10 text-red-200 ring-1 ring-red-500/25"
                : "glass text-text"
            }`}
          >
            <p className="leading-relaxed">{text}</p>
            {details && details.length > 0 && (
              <ul className="mt-2 space-y-1 border-t border-white/8 pt-2 text-xs text-text-muted">
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
    </div>
  );
}

/**
 * The ordered conversation: user turns on the right, the agent (Iris or the
 * active brand's steward) on the left with their avatar and name. Auto-scrolls
 * to the newest turn as the transcript grows.
 */
export function AgentTranscript({ turns }: { turns: Turn[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="flex flex-col gap-4">
      {turns.map((turn) => (
        <motion.div
          key={turn.id}
          initial={reduce ? false : { opacity: 0, y: 10, filter: "blur(4px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          transition={spring.card}
        >
          {turn.role === "user" ? (
            <UserBubble text={turn.text} />
          ) : (
            <AgentBubble
              kind={turn.kind}
              text={turn.text}
              details={turn.details}
              error={turn.error}
              speaker={turn.speaker}
            />
          )}
        </motion.div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
