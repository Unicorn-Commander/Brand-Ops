import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  getAgentTeam,
  listActivity,
  listAgentGovernance,
  listAgents,
  listAssets,
  listBrands,
  putAgentGovernance,
} from "../../api/client";
import { useActiveBrand } from "../../brand-context";
import { EmptyState } from "../../components/EmptyState";
import { MarkImg } from "../../components/MarkImg";
import { Spinner } from "../../components/Spinner";
import { assetsFor, relativeTime } from "../../v2/metrics";
import type {
  AgentGovernance,
  AgentGovernanceUpdate,
  AgentKind,
  AutonomyLevel,
  BrandSummary,
} from "../../lib/types";

const HOUSE_RULES = [
  ["HR-01", "Propose, don't publish", "Default posture. Production state changes need a human or an explicit auto-grant."],
  ["HR-02", "The gate is universal", "Every artifact through compliance: human-made, agent-made, or MCP-made."],
  ["HR-03", "Budgets are hard", "Per-agent weekly caps stop work when metered spend reaches the cap."],
  ["HR-04", "Everything signs its work", "Actor, model, refs, cost, and provenance are recorded on every artifact."],
] as const;

// The three autonomy postures, with the human label the selects show. Source of
// truth is governance.AUTONOMY_LEVELS on the backend; kept here for the <option>
// labels.
const AUTONOMY_OPTIONS: ReadonlyArray<[AutonomyLevel, string]> = [
  ["propose", "propose"],
  ["auto_with_review", "auto with review"],
  ["autonomous", "autonomous"],
];

// The default standing orders shown client-side until the API answers, so a card
// never flashes a stub. Mirrors governance.default_standing_orders so the text is
// the same before and after the first network read.
const DEFAULT_DIRECTOR_ORDERS = [
  "Research before proposing. Cite the dossier.",
  "Three directions minimum, one recommendation, defend it.",
  "Spawn a steward at every ratify; set its standing orders.",
].join("\n");

function defaultStandingOrders(kind: AgentKind, ref: string): string {
  if (kind === "director") return DEFAULT_DIRECTOR_ORDERS;
  if (kind === "steward") return "Guard palette, kit coverage, and drift for this brand.";
  if (kind === "specialist") return `Run ${ref} only when asked. Stay on brand. Surface results for review; do not publish.`;
  return "Stay on brand. Propose, do not publish. Surface work for review.";
}

/** A client-side default governance record, used until/unless the API has one. */
function defaultGovernance(kind: AgentKind, ref: string): AgentGovernance {
  return {
    agent_kind: kind,
    agent_ref: ref,
    autonomy: "propose",
    standing_orders: defaultStandingOrders(kind, ref),
    weekly_budget_cents: null,
    model: null,
    spend_cents_7d: 0,
    tokens_7d: 0,
    calls_7d: 0,
    is_default: true,
  };
}

/** Cents -> "$X.XX". */
function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function autonomyLabel(level: AutonomyLevel): string {
  return AUTONOMY_OPTIONS.find(([value]) => value === level)?.[1] ?? level;
}

export default function CrewPage() {
  const { activeSlug } = useActiveBrand();
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: ({ signal }) => listAgents(signal) });
  const assetsQ = useQuery({ queryKey: ["assets", "crew"], queryFn: ({ signal }) => listAssets({}, signal) });
  const activityQ = useQuery({ queryKey: ["activity", "crew"], queryFn: ({ signal }) => listActivity({ limit: 80 }, signal) });
  const governanceQ = useQuery({
    queryKey: ["governance"],
    queryFn: ({ signal }) => listAgentGovernance(signal),
  });
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

  // A lookup over the loaded governance rows, falling back to client-side
  // defaults so nothing ever renders a stub. Memoized off the query data.
  const governanceFor = useMemo(() => {
    const rows = governanceQ.data ?? [];
    const byKey = new Map<string, AgentGovernance>();
    for (const row of rows) byKey.set(`${row.agent_kind}:${row.agent_ref}`, row);
    return (kind: AgentKind, ref: string): AgentGovernance =>
      byKey.get(`${kind}:${ref}`) ?? defaultGovernance(kind, ref);
  }, [governanceQ.data]);

  // The 7-day spend headline: the sum across every governed agent's spend.
  const totalSpendCents = useMemo(
    () => (governanceQ.data ?? []).reduce((sum, row) => sum + (row.spend_cents_7d || 0), 0),
    [governanceQ.data],
  );

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
          <Stat value={formatCents(totalSpendCents)} label="spend · last 7 days" />
        </div>
      </div>

      <DirectorCard
        directorName={selected.team.director.name}
        directorRole={selected.team.director.role}
        brandCount={teams.length}
        governance={governanceFor("director", "iris")}
        recentActivity={dedupedActivity.slice(0, 4)}
      />

      <SectionTitle title="Brand stewards" note="One per brand. Standing orders and posture are editable; spend is metered." />
      <div className="crew-stewards" style={{ display: "grid", gap: 18 }}>
        {teams.map(({ brand, team }) => {
          const brandAssets = assetsFor(assets, brand.slug);
          const brandActivity = activity.filter((event) => event.brand === brand.slug);
          const approved = brandAssets.filter((asset) => asset.lifecycle_status === "approved").length;
          const approvedPct = brandAssets.length ? Math.round((approved / brandAssets.length) * 100) : null;
          return (
            <StewardCard
              key={brand.slug}
              brand={brand}
              stewardName={team.steward.name}
              stewardRole={team.steward.role}
              actions={brandActivity.length}
              approvedPct={approvedPct}
              governance={governanceFor("steward", brand.slug)}
            />
          );
        })}
      </div>

      <SectionTitle title="Specialists" note="Shared skills from /api/agents. Posture and 7-day spend per agent." />
      <div className="crew-specs" style={{ display: "grid", gap: 12 }}>
        {agents.slice(0, 6).map((agent) => {
          const gov = governanceFor("specialist", agent.name);
          return (
            <div key={agent.name} className="voice-card" style={{ padding: "13px 15px" }}>
              <b style={{ display: "block", fontFamily: "var(--c-font-mono)", fontSize: 12 }}>{agent.name}</b>
              <div style={{ color: "var(--c-ink-3)", fontSize: 10.5, marginTop: 2 }}>{agent.tier_required}</div>
              <div style={{ color: "var(--c-ink-2)", fontFamily: "var(--c-font-mono)", fontSize: 10, marginTop: 8 }}>
                {agent.scopes_required.length ? agent.scopes_required.join(", ") : "no scopes"}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
                <span className="chip">{autonomyLabel(gov.autonomy)}</span>
                <span className="chip">{formatCents(gov.spend_cents_7d)} · 7d</span>
              </div>
            </div>
          );
        })}
      </div>

      <SectionTitle title="Doors" note="Console and MCP/API access use the same gates." />
      <div className="crew-doors" style={{ display: "grid", gap: 18 }}>
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
      <div className="voice-card crew-rules" style={{ display: "grid", padding: 0 }}>
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

// ---------------------------------------------------------------------------
// Governance editing: a shared save hook + the Director and Steward cards.
// ---------------------------------------------------------------------------

/**
 * A mutation that saves one agent's governance and refreshes the list. `name` is
 * the human label used in the save toast; it falls back to the ref.
 */
function useSaveGovernance(kind: AgentKind, ref: string, name?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentGovernanceUpdate) => putAgentGovernance(kind, ref, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["governance"] });
      toast.success(name ? `Saved ${name}` : "Saved");
    },
    onError: () => {
      toast.error("Could not save. Try again.");
    },
  });
}

/**
 * Parse a dollars string ("12.50") into integer cents, or null for an empty
 * string (= no cap). Returns undefined for an unparseable value so the caller can
 * skip the field rather than send junk.
 */
function dollarsToCents(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  const dollars = Number(trimmed);
  if (!Number.isFinite(dollars) || dollars < 0) return undefined;
  return Math.round(dollars * 100);
}

function centsToDollarsInput(cents: number | null): string {
  return cents == null ? "" : (cents / 100).toFixed(2);
}

function DirectorCard({
  directorName,
  directorRole,
  brandCount,
  governance,
  recentActivity,
}: {
  directorName: string;
  directorRole: string;
  brandCount: number;
  governance: AgentGovernance;
  recentActivity: Array<{ id: string; actor: string; action: string; target: string | null; ts: string }>;
}) {
  const save = useSaveGovernance("director", "iris", directorName);
  const [autonomy, setAutonomy] = useState<AutonomyLevel>(governance.autonomy);
  const [orders, setOrders] = useState(governance.standing_orders);
  const [model, setModel] = useState(governance.model ?? "");
  const [budget, setBudget] = useState(centsToDollarsInput(governance.weekly_budget_cents));

  const budgetCents = governance.weekly_budget_cents;
  const overBudget = budgetCents != null && governance.spend_cents_7d > budgetCents;

  function onSave() {
    const parsedBudget = dollarsToCents(budget);
    const body: AgentGovernanceUpdate = {
      autonomy,
      standing_orders: orders,
      model: model.trim() === "" ? null : model.trim(),
    };
    if (parsedBudget !== undefined) body.weekly_budget_cents = parsedBudget;
    save.mutate(body);
  }

  return (
    <section className="voice-card crew-iris" style={{ marginTop: 28, display: "grid", padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "22px 26px", borderRight: "1px solid var(--c-hairline-soft)" }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          <span className="orb" style={{ width: 52, height: 52, position: "relative" }}>I<span className="dot dot-live" style={{ position: "absolute", right: 0, bottom: 1, border: "2.5px solid var(--c-surface)" }} /></span>
          <div>
            <span className="microlabel" style={{ color: "#b9a7ff" }}>Director</span>
            <b style={{ display: "block", fontFamily: "var(--c-font-display)", fontSize: 17 }}>{directorName}</b>
            <span style={{ color: "var(--c-ink-3)", fontSize: 12 }}>{directorRole}</span>
          </div>
          <div style={{ marginLeft: "auto", textAlign: "right" }}>
            <label className="microlabel" style={{ display: "block", marginBottom: 4 }}>autonomy</label>
            <select
              value={autonomy}
              onChange={(e) => setAutonomy(e.target.value as AutonomyLevel)}
              style={selectStyle}
            >
              {AUTONOMY_OPTIONS.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
            <div style={{ color: "var(--c-ink-3)", fontSize: 11, marginTop: 6 }}>active scope · {brandCount} brands</div>
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <EditableKV label="Model" placeholder="Iris default">
            <input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="Iris default"
              style={inputStyle}
            />
          </EditableKV>
          <KV k="Scope" v="all brands · spawn stewards · no publish" />
          <EditableKV label="Weekly budget">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ color: "var(--c-ink-3)", fontFamily: "var(--c-font-mono)", fontSize: 12 }}>$</span>
              <input
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="no cap"
                inputMode="decimal"
                style={{ ...inputStyle, width: 80 }}
              />
              <span style={{ color: overBudget ? "var(--c-danger, #d23)" : "var(--c-ink-3)", fontFamily: "var(--c-font-mono)", fontSize: 11 }}>
                {formatCents(governance.spend_cents_7d)} spent · 7d
              </span>
            </div>
          </EditableKV>
          <KV k="Memory" v="per-brand decisions retained where activity exists" />
        </div>
        <div style={{ marginTop: 14 }}>
          <label className="microlabel" style={{ display: "block", marginBottom: 6 }}>Standing orders</label>
          <textarea
            value={orders}
            onChange={(e) => setOrders(e.target.value)}
            rows={4}
            style={textareaStyle}
          />
          <SaveRow save={save} onSave={onSave} />
        </div>
      </div>
      <div style={{ padding: "22px 26px" }}>
        <span className="microlabel">Recent decisions</span>
        <div style={{ display: "grid", gap: 0, marginTop: 8 }}>
          {recentActivity.map((event) => (
            <div key={event.id} style={{ padding: "9px 0", borderBottom: "1px solid var(--c-hairline-soft)", fontSize: 12.5, color: "var(--c-ink-2)", display: "flex", gap: 10, alignItems: "baseline" }}>
              <span><b style={{ color: "var(--c-ink)" }}>{event.actor}</b> {event.action}{event.target ? ` · ${event.target}` : ""}</span>
              <span style={{ marginLeft: "auto", fontFamily: "var(--c-font-mono)", fontSize: 10, color: "var(--c-ink-3)", whiteSpace: "nowrap" }}>{relativeTime(event.ts)}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function StewardCard({
  brand,
  stewardName,
  stewardRole,
  actions,
  approvedPct,
  governance,
}: {
  brand: BrandSummary;
  stewardName: string;
  stewardRole: string;
  actions: number;
  approvedPct: number | null;
  governance: AgentGovernance;
}) {
  const save = useSaveGovernance("steward", brand.slug, brand.display_name);
  const [autonomy, setAutonomy] = useState<AutonomyLevel>(governance.autonomy);
  const [orders, setOrders] = useState(governance.standing_orders);

  function onSave() {
    save.mutate({ autonomy, standing_orders: orders });
  }

  return (
    <div className="voice-card" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 18px 12px" }}>
        <span className="mat dark" style={{ width: 44, height: 44, flex: "none", position: "relative", overflow: "visible" }}>
          <MarkImg brand={brand} />
          <span className="dot dot-live" style={{ position: "absolute", right: -3, bottom: -3, border: "2.5px solid var(--c-surface)" }} />
        </span>
        <div>
          <b style={{ display: "block", fontFamily: "var(--c-font-display)" }}>{brand.display_name}</b>
          <span style={{ color: "var(--c-ink-3)", fontSize: 11 }}>{stewardName} · {stewardRole}</span>
        </div>
      </div>
      <div style={{ padding: "0 18px 12px" }}>
        <label className="microlabel" style={{ display: "block", marginBottom: 6 }}>Standing orders</label>
        <textarea
          value={orders}
          onChange={(e) => setOrders(e.target.value)}
          rows={2}
          style={textareaStyle}
        />
      </div>
      <div style={{ display: "flex", borderTop: "1px solid var(--c-hairline-soft)" }}>
        <StripStat value={actions} label="actions/wk" />
        <StripStat value={approvedPct == null ? "—" : `${approvedPct}%`} label="approved" />
        <StripStat value={formatCents(governance.spend_cents_7d)} label="spend · 7d" />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "10px 18px", borderTop: "1px solid var(--c-hairline-soft)" }}>
        <select
          value={autonomy}
          onChange={(e) => setAutonomy(e.target.value as AutonomyLevel)}
          style={selectStyle}
        >
          {AUTONOMY_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={onSave}
          disabled={save.isPending}
          style={saveButtonStyle(save.isPending)}
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <Link to={`/b/${brand.slug}/studio`} style={{ marginLeft: "auto", fontSize: 11.5, fontWeight: 600, color: "var(--c-ink)", textDecoration: "none" }}>
          Talk →
        </Link>
      </div>
      {save.isError ? (
        <div style={{ padding: "0 18px 10px", color: "var(--c-danger, #d23)", fontSize: 11 }}>Could not save. Try again.</div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Small shared bits
// ---------------------------------------------------------------------------

const inputStyle: CSSProperties = {
  background: "var(--c-surface-2, transparent)",
  border: "1px solid var(--c-hairline-soft)",
  borderRadius: 6,
  padding: "4px 8px",
  fontFamily: "var(--c-font-mono)",
  fontSize: 12,
  color: "var(--c-ink)",
  textAlign: "right",
};

const selectStyle: CSSProperties = {
  background: "var(--c-surface-2, transparent)",
  border: "1px solid var(--c-hairline-soft)",
  borderRadius: 6,
  padding: "4px 8px",
  fontSize: 12,
  color: "var(--c-ink)",
};

const textareaStyle: CSSProperties = {
  width: "100%",
  background: "var(--c-surface-2, transparent)",
  border: "1px solid var(--c-hairline-soft)",
  borderRadius: 6,
  padding: "8px 10px",
  fontSize: 12,
  lineHeight: 1.5,
  color: "var(--c-ink-2)",
  resize: "vertical",
  boxSizing: "border-box",
  fontFamily: "inherit",
};

function saveButtonStyle(pending: boolean): CSSProperties {
  return {
    background: "var(--c-ink)",
    color: "var(--c-surface)",
    border: "none",
    borderRadius: 6,
    padding: "5px 14px",
    fontSize: 11.5,
    fontWeight: 600,
    cursor: pending ? "default" : "pointer",
    opacity: pending ? 0.6 : 1,
  };
}

function SaveRow({ save, onSave }: { save: { isPending: boolean; isError: boolean }; onSave: () => void }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10 }}>
      <button type="button" onClick={onSave} disabled={save.isPending} style={saveButtonStyle(save.isPending)}>
        {save.isPending ? "Saving…" : "Save"}
      </button>
      {save.isError ? <span style={{ color: "var(--c-danger, #d23)", fontSize: 11 }}>Could not save. Try again.</span> : null}
    </div>
  );
}

function EditableKV({ label, children }: { label: string; placeholder?: string; children: ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, fontSize: 12, padding: "7px 0", borderBottom: "1px solid var(--c-hairline-soft)" }}>
      <span style={{ color: "var(--c-ink-3)" }}>{label}</span>
      <div>{children}</div>
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
