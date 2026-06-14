import { useEffect, useState, type CSSProperties } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { intakeBrand, listBrands } from "../../api/client";
import type { IntakeResult } from "../../lib/types";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { brandColors } from "../../v2/metrics";
import { fontStack } from "../../v2/EnvProvider";
import { useViewMode } from "../../view-mode";
import { NEW_BRAND_MODES, pick } from "../../lib/labels";

type UiMode = "extend" | "describe" | "onboard";

// The three journeys in display order. Titles and bodies are mode-aware: they
// resolve from NEW_BRAND_MODES via pick(), so Simple reads plain goal-language
// ("Import from a website") and Advanced keeps the house voice ("From a brand").
// "onboard" leads because the import is the biggest single win.
const MODE_ORDER: UiMode[] = ["onboard", "extend", "describe"];

// The sessionStorage handoff (written by the Front Door / palette / showcase)
// speaks the Studio's vocabulary. Translate it to this page's UiMode so a chosen
// starting point pre-selects the matching journey when someone lands here.
const HANDOFF_KEY = "brand-ops.new-brand-mode";
function handoffToMode(value: string | null): UiMode | null {
  switch (value) {
    case "onboard":
      return "onboard";
    case "distinct":
      return "describe";
    case "consistent":
      return "extend";
    default:
      return null;
  }
}
function readHandoffMode(): UiMode | null {
  try {
    return handoffToMode(sessionStorage.getItem(HANDOFF_KEY));
  } catch {
    return null;
  }
}

const STAGES = [
  {
    no: "01",
    title: "Brief",
    hue: "#ff7a59",
    owner: "You x Iris",
    bullets: ["Capture intent from the form", "Lock parent and mode", "Record voice seeds"],
    artifacts: ["brief"],
  },
  {
    no: "02",
    title: "Research",
    hue: "#ffd166",
    owner: "Iris",
    bullets: ["Read parent context when selected", "Use URL hints when supplied", "Prepare draft constraints"],
    artifacts: ["context"],
  },
  {
    no: "03",
    title: "Directions",
    hue: "#7ce3a1",
    owner: "Iris",
    bullets: ["POST /brands/intake", "Draft palette, type, voice", "Return a real brand record"],
    artifacts: ["brand.yaml"],
  },
  {
    no: "04",
    title: "Ratify",
    hue: "#4cc9f0",
    owner: "You",
    bullets: ["Review the drafted identity", "Ratify or remix", "Only human gate in this run"],
    artifacts: ["rules"],
    gate: true,
  },
  {
    no: "05",
    title: "Steward + kit",
    hue: "#9b8cff",
    owner: "Queued agents",
    bullets: ["Spawn steward after ratify", "Generate required kit", "Run every asset through the gate"],
    artifacts: ["steward", "assets"],
  },
  {
    no: "06",
    title: "Live + on watch",
    hue: "#f15bb5",
    owner: "Steward",
    bullets: ["Draft book v1", "Open Studio/API/MCP doors", "Begin drift monitoring"],
    artifacts: ["book", "grade"],
  },
];

export default function NewBrandPage() {
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const { mode: viewMode } = useViewMode();
  // Pre-select the journey the operator chose upstream, else lead with the most
  // inviting one (import from a website).
  const [mode, setMode] = useState<UiMode>(() => readHandoffMode() ?? "onboard");
  // Consume the one-shot handoff so a later visit opens on the default.
  useEffect(() => {
    try {
      sessionStorage.removeItem(HANDOFF_KEY);
    } catch {
      // Best-effort; the in-memory state already reflects the handoff.
    }
  }, []);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [parent, setParent] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () =>
      intakeBrand({
        name,
        description: description || null,
        url: url || null,
        parent: mode === "extend" ? parent || null : parent || null,
        mode: mode === "onboard" ? "onboard" : "describe",
      }),
  });

  if (brandsQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandsQ.data) {
    return <EmptyState title="Brand intake unavailable" description="Could not load the brand list." />;
  }

  const running = mutation.isPending;
  const result = mutation.data;
  const drafted = result?.brand;
  const activeStage = running ? 2 : drafted ? 3 : 0;

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">New brand · intake run</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            {name || "Untitled draft"}
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            The Director drafts the identity, then writes the actual brand record. Stage 4 is the first human gate.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
          <span className="chip"><span className={running ? "dot dot-live" : "dot"} /> {running ? "intake running" : drafted ? "draft ready" : "ready"}</span>
          <span className="spec-note">No fake streaming: one real POST drives stages 2-3.</span>
        </div>
      </div>

      <div className="nb-modes" style={{ display: "grid", gap: 10, margin: "18px 0 24px" }}>
        {MODE_ORDER.map((id) => {
          const labels = NEW_BRAND_MODES[id];
          const selected = mode === id;
          // The import journey is the biggest single win, so when it is not the
          // chosen card it carries a soft accent treatment to draw the eye.
          const invite = id === "onboard" && !selected;
          return (
            <button
              key={id}
              type="button"
              onClick={() => setMode(id)}
              aria-pressed={selected}
              className={`voice-card ${selected ? "btn-ink" : ""}`}
              style={{
                position: "relative",
                textAlign: "left",
                cursor: "pointer",
                color: selected ? "#0a0a0e" : "var(--c-ink)",
                ...(invite
                  ? {
                      borderColor: "rgb(var(--accent) / 0.5)",
                      background:
                        "linear-gradient(180deg, rgb(var(--accent) / 0.08), transparent 70%)",
                      boxShadow: "var(--shadow-paper), 0 0 30px -14px rgb(var(--accent) / 0.55)",
                    }
                  : {}),
              }}
            >
              <b style={{ display: "block", fontFamily: "var(--c-font-display)" }}>
                {pick(labels.title, viewMode)}
              </b>
              <span
                style={{
                  display: "block",
                  marginTop: 4,
                  fontSize: 12,
                  color: selected ? "rgba(10,10,14,0.72)" : "var(--c-ink-3)",
                }}
              >
                {pick(labels.body, viewMode)}
              </span>
              {invite && (
                <span
                  style={{
                    display: "inline-block",
                    marginTop: 8,
                    fontFamily: "var(--c-font-mono)",
                    fontSize: 9.5,
                    textTransform: "uppercase",
                    letterSpacing: "0.04em",
                    color: "rgb(var(--accent))",
                  }}
                >
                  Fastest start
                </span>
              )}
            </button>
          );
        })}
      </div>

      <section className="nb-grid" style={{ display: "grid", gap: 24, alignItems: "start" }}>
        <form
          className="voice-card"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
          style={{ position: "sticky", top: 80 }}
        >
          <span className="microlabel">Brief</span>
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Tidepool Labs" style={inputStyle} />
          </label>
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A personal health-data vault. Calm trust, on their shore."
              rows={4}
              style={{ ...inputStyle, resize: "vertical" }}
            />
          </label>
          {mode === "onboard" && (
            <label style={{ display: "block", marginTop: 12 }}>
              <span className="microlabel">Website URL</span>
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com" style={inputStyle} />
            </label>
          )}
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Parent</span>
            <select value={parent ?? ""} onChange={(e) => setParent(e.target.value || null)} style={inputStyle}>
              <option value="">None / Magic Unicorn root</option>
              {brandsQ.data.map((brand) => (
                <option key={brand.slug} value={brand.slug}>
                  {brand.display_name}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-accent" type="submit" disabled={running} style={{ marginTop: 16 }}>
            {running ? "Drafting…" : "Run stages 2-3"}
          </button>
          {mutation.isError && (
            <p style={{ color: "var(--c-stop)", marginTop: 12 }}>
              {mutation.error instanceof Error ? mutation.error.message : "Intake failed."}
            </p>
          )}
        </form>

        <div>
          <section
            className="nb-pipeline"
            style={{
              position: "relative",
              display: "grid",
              gap: "38px 46px",
            }}
          >
            {STAGES.map((stage, index) => (
              <StageCard
                key={stage.no}
                stage={stage}
                index={index}
                active={index === activeStage}
                done={index < activeStage}
                waiting={index > activeStage}
              />
            ))}
          </section>

          <div style={{ display: "flex", alignItems: "baseline", gap: 14, margin: "34px 0 16px" }}>
            <h2 style={{ margin: 0, fontFamily: "var(--c-font-display)", fontSize: 21, fontWeight: 650, letterSpacing: "-0.015em" }}>
              Stage 3 output
            </h2>
            <span className="annotation">{drafted ? "Iris returned a persisted brand record." : "Run intake to render the real direction card."}</span>
          </div>

          {result ? (
            <DirectionCard result={result} />
          ) : (
            <div className="voice-card" style={{ minHeight: 220, display: "grid", placeItems: "center", color: "var(--c-ink-3)" }}>
              {running ? <Spinner size={24} /> : "No draft yet. Submit the brief to call /brands/intake."}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function StageCard({
  stage,
  index,
  active,
  done,
  waiting,
}: {
  stage: (typeof STAGES)[number];
  index: number;
  active: boolean;
  done: boolean;
  waiting: boolean;
}) {
  const gridArea = index < 3 ? `1 / ${index + 1}` : `2 / ${6 - index}`;
  const connector =
    index === 0 || index === 1 ? "right" : index === 2 ? "down" : index === 3 || index === 4 ? "left" : null;
  return (
    <div
      className="voice-card"
      style={{
        gridArea,
        position: "relative",
        display: "flex",
        flexDirection: "column",
        gap: 8,
        borderStyle: stage.gate ? "dashed" : "solid",
        borderColor: active ? "rgba(155,140,255,0.55)" : "var(--c-hairline-soft)",
        boxShadow: active ? "var(--shadow-paper), 0 0 44px -8px rgba(155,140,255,0.35)" : "var(--shadow-paper)",
      }}
    >
      <div style={{ position: "absolute", top: 0, left: 14, right: 14, height: 2.5, borderRadius: "0 0 4px 4px", background: stage.hue, opacity: 0.55 }} />
      {connector && (
        <span
          style={{
            position: "absolute",
            zIndex: 6,
            pointerEvents: "none",
            borderColor: done ? "var(--c-go)" : active ? "#b9a7ff" : "rgba(242,241,238,0.3)",
            ...(connector === "right" ? { right: -46, top: "50%", width: 46, borderTop: "2.5px solid" } : {}),
            ...(connector === "left" ? { left: -46, top: "50%", width: 46, borderTop: "2.5px dashed" } : {}),
            ...(connector === "down" ? { left: "50%", bottom: -38, height: 38, borderLeft: "2.5px solid" } : {}),
          }}
        />
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 10, color: stage.hue, border: `1px solid ${stage.hue}66`, borderRadius: 6, padding: "2px 6px" }}>{stage.no}</span>
        <b style={{ fontFamily: "var(--c-font-display)", fontSize: 14.5 }}>{stage.title}</b>
        <span style={{ marginLeft: "auto", fontFamily: "var(--c-font-mono)", fontSize: 9.5, textTransform: "uppercase", color: done ? "var(--c-go)" : active ? "#b9a7ff" : "var(--c-ink-3)" }}>
          {done ? "done" : active ? "active" : waiting ? "queued" : "ready"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11, color: "var(--c-ink-2)" }}>
        <span className="orb" style={{ width: 20, height: 20, fontSize: 9 }}>I</span>
        {stage.owner}
      </div>
      <ul style={{ margin: "2px 0 0", padding: 0, listStyle: "none" }}>
        {stage.bullets.map((bullet, bulletIndex) => (
          <li key={bullet} style={{ fontSize: 11.5, color: "var(--c-ink-2)", padding: "3px 0", lineHeight: 1.45 }}>
            <span style={{ color: done || (active && bulletIndex === 0) ? "var(--c-go)" : "var(--c-ink-3)" }}>■</span> {bullet}
          </li>
        ))}
      </ul>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: "auto", paddingTop: 8, borderTop: "1px solid var(--c-hairline-soft)" }}>
        {stage.artifacts.map((artifact) => (
          <span key={artifact} className="chip" style={{ fontFamily: "var(--c-font-mono)", fontSize: 9.5 }}>
            {artifact}{done ? " ✓" : ""}
          </span>
        ))}
        {stage.gate && <span style={{ marginLeft: "auto", color: "var(--c-warn)", fontFamily: "var(--c-font-mono)", fontSize: 10 }}>human gate</span>}
      </div>
    </div>
  );
}

function DirectionCard({ result }: { result: IntakeResult }) {
  const brand = result.brand;
  const colors = brandColors(brand);
  const display = fontStack(brand.visual.typography.display_font);
  return (
    <div className="voice-card nb-direction" style={{ display: "grid", gap: 18 }}>
      <div
        style={{
          minHeight: 250,
          borderRadius: "var(--radius)",
          padding: 20,
          display: "flex",
          flexDirection: "column",
          background: `radial-gradient(420px 300px at 14% -8%, ${colors.primary}88, transparent 60%), ${colors.bg}`,
          color: colors.ink,
          overflow: "hidden",
        }}
      >
        <span style={{ width: 46, height: 46, borderRadius: 12, display: "grid", placeItems: "center", background: "rgba(255,255,255,0.1)", color: colors.accent, fontFamily: display, fontSize: 24, fontWeight: 800 }}>
          {brand.display_name.charAt(0)}
        </span>
        <div style={{ marginTop: "auto", fontFamily: display, fontSize: 30, lineHeight: 1.05, fontWeight: 750 }}>{brand.display_name}</div>
        <div style={{ marginTop: 4, opacity: 0.72, fontSize: 12 }}>{brand.tagline}</div>
        <div style={{ display: "flex", height: 7, margin: "18px -20px -20px" }}>
          {[colors.bg, colors.primary, colors.accent, colors.ink, colors.muted].map((color) => (
            <span key={color} style={{ flex: 1, background: color }} />
          ))}
        </div>
      </div>
      <div>
        <span className="microlabel">Direction card · live token preview</span>
        <p style={{ color: "var(--c-ink-2)", lineHeight: 1.55 }}>{result.draft.rationale}</p>
        <p style={{ color: "var(--c-ink-3)", fontSize: 12 }}>
          {brand.visual.typography.display_font} display · {brand.visual.typography.primary_font} body
        </p>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
          {result.draft.voice_tone.map((tone) => <span key={tone} className="chip">{tone}</span>)}
        </div>
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          <a className="btn btn-ink" href={`/b/${brand.slug}/overview`}>Ratify</a>
          <a className="btn btn-ghost" href={`/b/${brand.slug}/studio?context=remix`}>Remix</a>
        </div>
        {result.notes.length > 0 && <p className="spec-note" style={{ marginTop: 14 }}>{result.notes.join(" · ")}</p>}
      </div>
    </div>
  );
}

const inputStyle = {
  width: "100%",
  marginTop: 6,
  borderRadius: 10,
  border: "1px solid var(--b-hairline)",
  background: "rgba(255,255,255,0.03)",
  color: "var(--b-ink)",
  padding: "10px 12px",
  font: "inherit",
} as const satisfies CSSProperties;
