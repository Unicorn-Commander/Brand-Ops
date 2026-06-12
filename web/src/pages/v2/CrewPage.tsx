import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAgentTeam, listActivity, listAgents, listAssets, listBrands } from "../../api/client";
import { useActiveBrand } from "../../brand-context";
import { EmptyState } from "../../components/EmptyState";
import { MarkImg } from "../../components/MarkImg";
import { Spinner } from "../../components/Spinner";
import { assetsFor, relativeTime } from "../../v2/metrics";

const HOUSE_RULES = [
  ["HR-01", "Propose, don't publish", "Default posture. Production state changes need a human or an explicit auto-grant."],
  ["HR-02", "The gate is universal", "Every artifact through compliance: human-made, agent-made, or MCP-made."],
  ["HR-03", "Budgets are hard", "Per-agent weekly caps stop work when metered spend reaches the cap."],
  ["HR-04", "Everything signs its work", "Actor, model, refs, cost, and provenance are recorded on every artifact."],
] as const;

const STANDING_ORDERS = [
  "TODO(backend): Research before proposing. Cite the dossier.",
  "TODO(backend): Three directions minimum, one recommendation, defend it.",
  "TODO(backend): Spawn a steward at every ratify; set its standing orders.",
];

export default function CrewPage() {
  const { activeSlug } = useActiveBrand();
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: ({ signal }) => listAgents(signal) });
  const assetsQ = useQuery({ queryKey: ["assets", "crew"], queryFn: ({ signal }) => listAssets({}, signal) });
  const activityQ = useQuery({ queryKey: ["activity", "crew"], queryFn: ({ signal }) => listActivity({ limit: 80 }, signal) });
  const mcpQ = useQuery({
    queryKey: ["mcp-manifest"],
    queryFn: async ({ signal }) => {
      const res = await fetch("/.well-known/mcps.json", { signal });
      if (!res.ok) return null;
      return (await res.json()) as { tools?: unknown[]; resources?: unknown[] };
    },
  });
  const teamsQ = useQuery({
    queryKey: ["agent-teams", "all"],
    queryFn: async ({ signal }) => {
      const brands = await listBrands(signal);
      return Promise.all(brands.map(async (brand) => ({ brand, team: await getAgentTeam(brand.slug, signal) })));
    },
  });

  if (brandsQ.isLoading || agentsQ.isLoading || activityQ.isLoading || assetsQ.isLoading || teamsQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandsQ.data || !agentsQ.data || !activityQ.data || !assetsQ.data || !teamsQ.data) {
    return <EmptyState title="Crew unavailable" description="The roster could not be loaded from the API." />;
  }

  const agents = agentsQ.data;
  const activity = activityQ.data;
  const assets = assetsQ.data;
  const teams = teamsQ.data;
  const selected = teams.find((row) => row.brand.slug === activeSlug) ?? teams[0];
  const dedupedActivity = [...new Map(activity.map((event) => [`${event.actor}:${event.action}:${event.target ?? ""}`, event])).values()];
  const onDuty = agents.length + teams.length + teams.reduce((sum, row) => sum + row.team.governance.total, 0);
  const weekCutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const weeklyActions = activity.filter((event) => new Date(event.ts).getTime() >= weekCutoff).length;
  const reviewItems = assets.filter((asset) => asset.lifecycle_status === "needs_review").length;
  const toolCount = mcpQ.data?.tools?.length ?? 0;

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">Crew · agents on staff</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            Staff, not features.
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            Every agent is hired, scoped, and accountable, with visible work and the same gates as humans.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 24, flexWrap: "wrap" }}>
          <Stat value={onDuty} label="on duty" />
          <Stat value={weeklyActions} label="actions this week" />
          <Stat value={reviewItems} label="need review" />
          <Stat value="—" label="spend not yet metered" />
        </div>
      </div>

      <section className="voice-card" style={{ marginTop: 28, display: "grid", gridTemplateColumns: "1.2fr 1fr", padding: 0, overflow: "hidden" }}>
        <div style={{ padding: "22px 26px", borderRight: "1px solid var(--c-hairline-soft)" }}>
          <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
            <span className="orb" style={{ width: 52, height: 52, position: "relative" }}>I<span className="dot dot-live" style={{ position: "absolute", right: 0, bottom: 1, border: "2.5px solid var(--c-surface)" }} /></span>
            <div>
              <span className="microlabel" style={{ color: "#b9a7ff" }}>Director</span>
              <b style={{ display: "block", fontFamily: "var(--c-font-display)", fontSize: 17 }}>{selected.team.director.name}</b>
              <span style={{ color: "var(--c-ink-3)", fontSize: 12 }}>{selected.team.director.role}</span>
            </div>
            <div style={{ marginLeft: "auto", textAlign: "right" }}>
              <span className="chip">auto with review · TODO(backend)</span>
              <div style={{ color: "var(--c-ink-3)", fontSize: 11, marginTop: 6 }}>active scope · {teams.length} brands</div>
            </div>
          </div>
          <div style={{ marginTop: 14 }}>
            <KV k="Model" v="TODO(backend): director config endpoint" mono />
            <KV k="Scope" v="all brands · spawn stewards · no publish" />
            <KV k="Budget" v="not yet metered" mono />
            <KV k="Memory" v="per-brand decisions retained where activity exists" />
          </div>
          <ul style={{ margin: "14px 0 0", padding: 0, listStyle: "none" }}>
            {STANDING_ORDERS.map((order) => (
              <li key={order} style={{ fontSize: 12, color: "var(--c-ink-2)", padding: "4px 0", lineHeight: 1.5 }}>§ {order}</li>
            ))}
          </ul>
        </div>
        <div style={{ padding: "22px 26px" }}>
          <span className="microlabel">Recent decisions</span>
          <div style={{ display: "grid", gap: 0, marginTop: 8 }}>
            {dedupedActivity.slice(0, 4).map((event) => (
              <div key={event.id} style={{ padding: "9px 0", borderBottom: "1px solid var(--c-hairline-soft)", fontSize: 12.5, color: "var(--c-ink-2)", display: "flex", gap: 10, alignItems: "baseline" }}>
                <span><b style={{ color: "var(--c-ink)" }}>{event.actor}</b> {event.action}{event.target ? ` · ${event.target}` : ""}</span>
                <span style={{ marginLeft: "auto", fontFamily: "var(--c-font-mono)", fontSize: 10, color: "var(--c-ink-3)", whiteSpace: "nowrap" }}>{relativeTime(event.ts)}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <SectionTitle title="Brand stewards" note="One per brand. Counts come from activity and lifecycle state." />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18 }}>
        {teams.map(({ brand, team }) => {
          const brandAssets = assetsFor(assets, brand.slug);
          const brandActivity = activity.filter((event) => event.brand === brand.slug);
          const approved = brandAssets.filter((asset) => asset.lifecycle_status === "approved").length;
          const approvedPct = brandAssets.length ? Math.round((approved / brandAssets.length) * 100) : null;
          return (
            <div key={brand.slug} className="voice-card" style={{ padding: 0, overflow: "hidden" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 18px 12px" }}>
                <span className="mat dark" style={{ width: 44, height: 44, flex: "none", position: "relative", overflow: "visible" }}>
                  <MarkImg brand={brand} />
                  <span className="dot dot-live" style={{ position: "absolute", right: -3, bottom: -3, border: "2.5px solid var(--c-surface)" }} />
                </span>
                <div>
                  <b style={{ display: "block", fontFamily: "var(--c-font-display)" }}>{brand.display_name}</b>
                  <span style={{ color: "var(--c-ink-3)", fontSize: 11 }}>{team.steward.name} · {team.steward.role}</span>
                </div>
              </div>
              <p style={{ padding: "0 18px 10px", margin: 0, fontSize: 11.5, color: "var(--c-ink-2)", lineHeight: 1.55 }}>
                <span style={{ color: "var(--c-ink-3)" }}>Standing orders:</span> TODO(backend) guard palette, kit coverage, and drift for this brand.
              </p>
              <div style={{ display: "flex", borderTop: "1px solid var(--c-hairline-soft)" }}>
                <StripStat value={brandActivity.length} label="actions/wk" />
                <StripStat value={approvedPct == null ? "—" : `${approvedPct}%`} label="approved" />
                <StripStat value="—" label="spend" />
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 18px", borderTop: "1px solid var(--c-hairline-soft)" }}>
                <span className="chip">auto with review · TODO(backend)</span>
                <Link to={`/b/${brand.slug}/studio`} style={{ marginLeft: "auto", fontSize: 11.5, fontWeight: 600, color: "var(--c-ink)", textDecoration: "none" }}>
                  Talk →
                </Link>
              </div>
            </div>
          );
        })}
      </div>

      <SectionTitle title="Specialists" note="Shared skills from /api/agents." />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 12 }}>
        {agents.slice(0, 6).map((agent) => (
          <div key={agent.name} className="voice-card" style={{ padding: "13px 15px" }}>
            <b style={{ display: "block", fontFamily: "var(--c-font-mono)", fontSize: 12 }}>{agent.name}</b>
            <div style={{ color: "var(--c-ink-3)", fontSize: 10.5, marginTop: 2 }}>{agent.tier_required}</div>
            <div style={{ color: "var(--c-ink-2)", fontFamily: "var(--c-font-mono)", fontSize: 10, marginTop: 8 }}>
              {agent.scopes_required.length ? agent.scopes_required.join(", ") : "no scopes"}
            </div>
          </div>
        ))}
      </div>

      <SectionTitle title="Doors" note="Console and MCP/API access use the same gates." />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18 }}>
        <Door title="Console" chip="you" stats={[["1", "human"], [String(reviewItems), "awaiting sign-off"]]} body="This GUI: approvals and human gates." />
        <Door title="MCP" chip={`${toolCount || "—"} tools`} glow stats={[[String(toolCount || "—"), "tools"], [String(mcpQ.data?.resources?.length ?? "—"), "resources"]]} body="Agent door: /.well-known/mcps.json." />
        <Door title="API" chip="REST" stats={[[String(activity.length), "recent actions"], [String(teams.length), "brands"]]} body="Service door: suite apps reading brand truth." />
      </div>
      <div className="voice-card" style={{ marginTop: 18, padding: "6px 0" }}>
        <span className="microlabel" style={{ display: "block", padding: "10px 20px" }}>Traffic feed · recent actions</span>
        {activity.slice(0, 5).map((event) => (
          <div key={event.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 20px", fontFamily: "var(--c-font-mono)", fontSize: 10.5, color: "var(--c-ink-2)", borderTop: "1px solid var(--c-hairline-soft)" }}>
            <span style={{ color: "var(--c-ink)", fontWeight: 600 }}>{event.actor}</span>
            <span className="chip">{event.actor_kind}</span>
            <span>{event.action}{event.target ? ` · ${event.target}` : ""}</span>
            <span style={{ marginLeft: "auto", color: "var(--c-ink-3)" }}>{relativeTime(event.ts)}</span>
          </div>
        ))}
      </div>

      <SectionTitle title="House rules" note="The contract every agent signs." />
      <div className="voice-card" style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", padding: 0 }}>
        {HOUSE_RULES.map(([no, title, body], index) => (
          <div key={no} style={{ padding: "16px 20px", borderRight: index === HOUSE_RULES.length - 1 ? undefined : "1px solid var(--c-hairline-soft)" }}>
            <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 9.5, color: "#b9a7ff", display: "block", marginBottom: 6 }}>{no}</span>
            <b style={{ display: "block", fontFamily: "var(--c-font-display)", fontSize: 13, marginBottom: 4 }}>{title}</b>
            <p style={{ margin: 0, fontSize: 11.5, color: "var(--c-ink-2)", lineHeight: 1.55 }}>{body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return <div><b style={{ display: "block", fontSize: 22 }}>{value}</b><span className="microlabel">{label}</span></div>;
}

function StripStat({ value, label }: { value: string | number; label: string }) {
  return (
    <div style={{ flex: 1, padding: "9px 0", textAlign: "center", borderRight: "1px solid var(--c-hairline-soft)" }}>
      <b style={{ display: "block", fontFamily: "var(--c-font-mono)", fontSize: 12 }}>{value}</b>
      <span style={{ fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--c-ink-3)" }}>{label}</span>
    </div>
  );
}

function KV({ k, v, mono = false }: { k: string; v: string; mono?: boolean }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 12, padding: "7px 0", borderBottom: "1px solid var(--c-hairline-soft)" }}>
      <span style={{ color: "var(--c-ink-3)" }}>{k}</span>
      <span style={{ fontWeight: 600, textAlign: "right", fontFamily: mono ? "var(--c-font-mono)" : undefined }}>{v}</span>
    </div>
  );
}

function SectionTitle({ title, note }: { title: string; note: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 14, margin: "30px 0 14px" }}>
      <h2 style={{ margin: 0, fontFamily: "var(--c-font-display)", fontSize: 19, fontWeight: 650, letterSpacing: "-0.015em" }}>{title}</h2>
      <span className="annotation">{note}</span>
    </div>
  );
}

function Door({
  title,
  chip,
  stats,
  body,
  glow = false,
}: {
  title: string;
  chip: string;
  stats: Array<[string, string]>;
  body: string;
  glow?: boolean;
}) {
  return (
    <div
      className="voice-card"
      style={{
        borderColor: glow ? "rgba(155,140,255,0.4)" : undefined,
        boxShadow: glow ? "var(--shadow-paper), 0 0 40px -12px rgba(155,140,255,0.3)" : undefined,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <b style={{ fontFamily: "var(--c-font-display)", fontSize: 14.5 }}>{title}</b>
        <span className="chip" style={{ marginLeft: "auto" }}>{chip}</span>
      </div>
      <div style={{ fontSize: 11.5, color: "var(--c-ink-3)", marginTop: 3 }}>{body}</div>
      <div style={{ marginTop: 12, display: "flex", gap: 16 }}>
        {stats.map(([value, label]) => (
          <div key={label}>
            <b style={{ display: "block", fontFamily: "var(--c-font-mono)", fontSize: 14 }}>{value}</b>
            <span style={{ fontSize: 9.5, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--c-ink-3)" }}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
