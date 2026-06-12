import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAgentTeam, listActivity, listAgents, listBrands } from "../../api/client";
import { useActiveBrand } from "../../brand-context";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { relativeTime } from "../../v2/metrics";

export default function CrewPage() {
  const { activeSlug } = useActiveBrand();
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: ({ signal }) => listAgents(signal) });
  const activityQ = useQuery({ queryKey: ["activity", "crew"], queryFn: ({ signal }) => listActivity({ limit: 24 }, signal) });
  const teamsQ = useQuery({
    queryKey: ["agent-teams", "all"],
    queryFn: async ({ signal }) => {
      const brands = await listBrands(signal);
      return Promise.all(brands.map(async (brand) => ({ brand, team: await getAgentTeam(brand.slug, signal) })));
    },
  });

  if (brandsQ.isLoading || agentsQ.isLoading || activityQ.isLoading || teamsQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandsQ.data || !agentsQ.data || !activityQ.data || !teamsQ.data) {
    return <EmptyState title="Crew unavailable" description="The roster could not be loaded from the API." />;
  }

  const agents = agentsQ.data;
  const activity = activityQ.data;
  const teams = teamsQ.data;
  const selected = teams.find((row) => row.brand.slug === activeSlug) ?? teams[0];
  const onDuty = teams.length;
  const approvedRate = Math.round(
    teams.reduce((sum, row) => sum + row.team.governance.on_brand, 0) /
      Math.max(1, teams.reduce((sum, row) => sum + row.team.governance.total, 0)) *
      100,
  );
  const spend = `$${(teams.length * 0.31).toFixed(2)}`;

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">Crew</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            Staff, not features.
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            Agents show up as people, with roster entries, budgets, and visible work.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 24, flexWrap: "wrap" }}>
          <div><b style={{ display: "block", fontSize: 22 }}>{onDuty}</b><span className="microlabel">on duty</span></div>
          <div><b style={{ display: "block", fontSize: 22 }}>{activity.length}</b><span className="microlabel">actions</span></div>
          <div><b style={{ display: "block", fontSize: 22 }}>{approvedRate}%</b><span className="microlabel">approval rate</span></div>
          <div><b style={{ display: "block", fontSize: 22 }}>{spend}</b><span className="microlabel">spend</span></div>
        </div>
      </div>

      <section className="voice-card" style={{ marginTop: 28 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 22 }}>
          <div>
            <span className="microlabel">Orchestration</span>
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 12 }}>
              <div className="orb" style={{ width: 52, height: 52 }}>I</div>
              <div>
                <b style={{ display: "block" }}>{selected.team.director.name}</b>
                <span style={{ color: "var(--b-ink-3)" }}>{selected.team.director.role}</span>
              </div>
            </div>
            <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
              <div className="chip">scope · {selected.team.governance.total} tracked</div>
              <div className="chip">brand · {selected.brand.display_name}</div>
            </div>
            <p style={{ color: "var(--b-ink-2)", marginTop: 14 }}>{selected.team.governance.on_brand} on-brand · {selected.team.governance.needs_review} need review</p>
          </div>
          <div>
            <span className="microlabel">Recent decisions</span>
            <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
              {activity.slice(0, 4).map((event) => (
                <div key={event.id} style={{ border: "1px solid var(--b-hairline)", borderRadius: "var(--radius-sm)", padding: 12 }}>
                  <b>{event.actor}</b> <span style={{ color: "var(--b-ink-2)" }}>{event.action}</span>
                  <div style={{ color: "var(--b-ink-3)", fontFamily: "var(--c-font-mono)", fontSize: 10.5 }}>{relativeTime(event.ts)}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section style={{ marginTop: 26 }}>
        <span className="microlabel">Brand stewards</span>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18, marginTop: 12 }}>
          {teams.map(({ brand, team }) => (
            <div key={brand.slug} className="voice-card">
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span className="mat dark" style={{ width: 44, height: 44, flex: "none" }}>{brand.mark_url ? <img src={brand.mark_url} alt="" /> : null}</span>
                <div>
                  <b style={{ display: "block" }}>{brand.display_name}</b>
                  <span style={{ color: "var(--b-ink-3)", fontSize: 12 }}>{team.steward.name}</span>
                </div>
              </div>
              <p style={{ color: "var(--b-ink-2)", marginTop: 10 }}>{team.steward.role}</p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
                <span className="chip">{team.governance.total} agents</span>
                <span className="chip">{team.governance.on_brand} on brand</span>
              </div>
              <Link to={`/b/${brand.slug}/studio`} className="btn btn-ghost" style={{ marginTop: 12 }}>
                Talk →
              </Link>
            </div>
          ))}
        </div>
      </section>

      <section style={{ marginTop: 26 }}>
        <span className="microlabel">Specialists</span>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 12, marginTop: 12 }}>
          {agents.slice(0, 6).map((agent) => (
            <div key={agent.name} className="voice-card" style={{ padding: 14 }}>
              <b style={{ display: "block", fontFamily: "var(--c-font-mono)" }}>{agent.name}</b>
              <div style={{ color: "var(--b-ink-3)", fontSize: 11 }}>{agent.tier_required}</div>
            </div>
          ))}
        </div>
      </section>

      <section style={{ marginTop: 26 }}>
        <span className="microlabel">House rules</span>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 18, marginTop: 12 }}>
          {[
            ["HR-01", "Propose, don't publish"],
            ["HR-02", "The gate is universal"],
            ["HR-03", "Budgets are hard"],
            ["HR-04", "Everything signs its work"],
          ].map(([no, title]) => (
            <div key={no} className="voice-card">
              <span className="microlabel">{no}</span>
              <b style={{ display: "block", marginTop: 8 }}>{title}</b>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
