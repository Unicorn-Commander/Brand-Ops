import { useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Globe, MessageCircle, Sparkles, Wand2 } from "lucide-react";
import { staggerContainer, staggerItem } from "../lib/motion";
import { NEW_BRAND_MODES, pick } from "../lib/labels";
import { useViewMode } from "../view-mode";

/**
 * The first-run front door for an empty workspace.
 *
 * A brand-new owner with zero brands should never meet a blank command deck.
 * This is a goal-first surface (the research is clear: lead with natural
 * language, offer a few broad outcome-language starters, and present concrete
 * choices, never an empty canvas). It renders three things, top to bottom:
 *
 *   1. A warm headline + a plain-language line that invites a sentence.
 *   2. A natural-language entry (the omnibar look) with broad starter chips
 *      above it. Free text routes straight into the Studio, where Iris takes
 *      over (Studio reads the `?prompt=` handoff).
 *   3. Three goal cards = the three ways to begin a brand, in plain
 *      goal-language (Simple) or the house voice (Advanced) via NEW_BRAND_MODES.
 *
 * Copy honors the house rule: no em-dashes, no marketing-speak. Motion is the
 * shared blur-rise stagger, gated on prefers-reduced-motion.
 */

const STARTERS = [
  "Make a logo for my business",
  "Build a full brand kit",
  "Design social posts",
  "Write my brand voice",
];

/** Where a free-text entry lands. Studio parses `?prompt=` and lets Iris run. */
const STUDIO = "/b/magic-unicorn/studio";

interface GoalCardProps {
  icon: typeof Wand2;
  title: string;
  body: string;
  cta: string;
  accent: string;
  feature?: boolean;
  onClick: () => void;
}

function GoalCard({ icon: Icon, title, body, cta, accent, feature, onClick }: GoalCardProps) {
  const reduce = useReducedMotion();
  return (
    <motion.button
      type="button"
      variants={staggerItem}
      onClick={onClick}
      whileHover={reduce ? undefined : { y: -4 }}
      transition={{ type: "spring", stiffness: 320, damping: 28 }}
      className="voice-card"
      style={{
        textAlign: "left",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "22px 22px 20px",
        background: feature
          ? "linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02))"
          : "rgba(255,255,255,0.025)",
        border: feature
          ? `1px solid color-mix(in srgb, ${accent} 40%, var(--c-hairline))`
          : "1px solid var(--c-hairline)",
        boxShadow: feature
          ? `var(--shadow-paper), 0 0 44px -18px ${accent}`
          : "var(--shadow-paper)",
        position: "relative",
      }}
    >
      {feature && (
        <span
          className="microlabel"
          style={{
            position: "absolute",
            top: 16,
            right: 18,
            color: accent,
            letterSpacing: "0.12em",
          }}
        >
          Fastest
        </span>
      )}
      <span
        style={{
          width: 42,
          height: 42,
          borderRadius: 11,
          display: "grid",
          placeItems: "center",
          background: `color-mix(in srgb, ${accent} 16%, transparent)`,
          color: accent,
          flex: "none",
        }}
      >
        <Icon size={20} strokeWidth={1.9} />
      </span>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
        <h3
          style={{
            margin: 0,
            fontFamily: "var(--c-font-display)",
            fontSize: 17,
            fontWeight: 650,
            letterSpacing: "-0.015em",
            color: "var(--c-ink)",
          }}
        >
          {title}
        </h3>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.55, color: "var(--c-ink-2)" }}>{body}</p>
      </div>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 12.5,
          fontWeight: 600,
          color: feature ? accent : "var(--c-ink)",
        }}
      >
        {cta}
        <ArrowRight size={14} strokeWidth={2.2} />
      </span>
    </motion.button>
  );
}

export default function FrontDoor() {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const { mode, isSimple } = useViewMode();
  const [text, setText] = useState("");

  /** Hand a free-text intent to the Studio, where Iris takes it from there. */
  function go(prompt: string) {
    const value = prompt.trim();
    if (!value) return;
    navigate(`${STUDIO}?prompt=${encodeURIComponent(value)}`);
  }

  /** Start one of the three new-brand journeys via the sessionStorage handoff
   *  the NewBrand page reads ("distinct" / "onboard"), then route there. */
  function startBrand(handoff: "distinct" | "onboard") {
    try {
      sessionStorage.setItem("brand-ops.new-brand-mode", handoff);
    } catch {
      // Best-effort: if storage is blocked, NewBrand simply opens unframed.
    }
    navigate("/brands/new");
  }

  const scratch = NEW_BRAND_MODES.describe;
  const onboard = NEW_BRAND_MODES.onboard;

  // Honor mode: Simple is warm and guided; Advanced keeps the same surface but
  // terser and in the house voice (the card labels already switch via pick).
  const headline = isSimple ? "Let's set up your brand." : "Stand up a brand.";
  const subline = isSimple
    ? "Tell me what you're working on and I'll take it from there."
    : "Describe it in a sentence, or pick a starting point.";

  const initial = reduce ? false : "hidden";

  return (
    <motion.div
      variants={staggerContainer}
      initial={initial}
      animate="show"
      style={{
        maxWidth: 760,
        margin: "0 auto",
        padding: "72px 0 80px",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
      }}
    >
      <motion.span variants={staggerItem} className="microlabel" style={{ marginBottom: 18 }}>
        Welcome to Brand-Ops
      </motion.span>

      <motion.h1
        variants={staggerItem}
        style={{
          margin: 0,
          fontFamily: "var(--c-font-display)",
          fontWeight: 700,
          fontSize: 42,
          lineHeight: 1.05,
          letterSpacing: "-0.028em",
          color: "var(--c-ink)",
        }}
      >
        {headline}
      </motion.h1>

      <motion.p
        variants={staggerItem}
        style={{
          margin: "14px 0 0",
          fontSize: 16,
          lineHeight: 1.55,
          color: "var(--c-ink-2)",
          maxWidth: 480,
        }}
      >
        {subline}
      </motion.p>

      {/* Broad starter chips sit ABOVE the input (NN/g): they show the kinds of
          things to say, and tapping one submits straight into the Studio. */}
      <motion.div
        variants={staggerItem}
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 8,
          justifyContent: "center",
          margin: "26px 0 14px",
        }}
      >
        {STARTERS.map((s) => (
          <button
            key={s}
            type="button"
            className="chip"
            onClick={() => go(s)}
            style={{ cursor: "pointer", background: "transparent" }}
          >
            {s}
          </button>
        ))}
      </motion.div>

      {/* The natural-language entry, in the omnibar look. */}
      <motion.form
        variants={staggerItem}
        className="omnibar"
        style={{ width: "100%", maxWidth: 600 }}
        onSubmit={(event) => {
          event.preventDefault();
          go(text);
        }}
      >
        <span className="orb">I</span>
        <input
          autoFocus={!reduce}
          aria-label="Describe what you want to make"
          placeholder="Make a logo for my coffee shop..."
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button className="btn btn-ink" type="submit" style={{ flex: "none" }}>
          <Sparkles size={15} strokeWidth={2} />
          Start
        </button>
      </motion.form>

      <motion.span
        variants={staggerItem}
        style={{ margin: "28px 0 16px", fontSize: 12.5, color: "var(--c-ink-3)" }}
      >
        Or pick a starting point
      </motion.span>

      {/* The three journeys as goal cards. */}
      <motion.div
        variants={staggerContainer}
        initial={initial}
        animate="show"
        style={
          {
            display: "grid",
            gap: 16,
            width: "100%",
            gridTemplateColumns: "repeat(3, 1fr)",
          } as CSSProperties
        }
      >
        <GoalCard
          icon={Globe}
          accent="#4cc9f0"
          feature
          title={pick(onboard.title, mode)}
          body={
            isSimple
              ? "Paste your website and we'll pull your logo, colors, and fonts."
              : pick(onboard.body, mode)
          }
          cta="Import from a site"
          onClick={() => startBrand("onboard")}
        />
        <GoalCard
          icon={Wand2}
          accent="#9b8cff"
          title={pick(scratch.title, mode)}
          body={pick(scratch.body, mode)}
          cta="Start from scratch"
          onClick={() => startBrand("distinct")}
        />
        <GoalCard
          icon={MessageCircle}
          accent="#6fdb96"
          title={isSimple ? "Show me how" : "Talk to Iris"}
          body={
            isSimple
              ? "Not sure where to start? Iris will walk you through it step by step."
              : "Open a guided conversation and let the Director frame the work."
          }
          cta="Talk to Iris"
          onClick={() => navigate(STUDIO)}
        />
      </motion.div>
    </motion.div>
  );
}
