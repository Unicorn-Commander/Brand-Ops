import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  getBrand,
  getBrandCompliance,
  getBrandTokens,
  getAgentTeam,
  listActivity,
  listAssets,
  listAssetTypes,
} from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { fontNote } from "../../v2/EnvProvider";
import {
  assetsFor,
  complianceScore,
  coverageFor,
  displayActivity,
  flaggedCount,
  gradeFromCoverage,
  relativeTime,
} from "../../v2/metrics";

const REQUIRED_LABELS: Record<string, string> = {
  logo: "Logo",
  app_icon: "App icon",
  og_image: "OG image",
  social_card: "Social card",
  podcast_cover: "Podcast cover",
  avatar: "Avatar",
};

function textColor(hex: string): string {
  const h = hex.replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return "#ffffff";
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return (r * 299 + g * 587 + b * 114) / 1000 > 150 ? "#11151a" : "#f8fafc";
}

export default function BrandHQPage() {
  const { slug = "" } = useParams();
  const brandQ = useQuery({ queryKey: ["brand", slug], queryFn: ({ signal }) => getBrand(slug, signal) });
  const tokensQ = useQuery({ queryKey: ["brand-tokens", slug], queryFn: ({ signal }) => getBrandTokens(slug, signal) });
  const assetsQ = useQuery({ queryKey: ["assets", slug], queryFn: ({ signal }) => listAssets({ brand: slug }, signal) });
  const typesQ = useQuery({ queryKey: ["asset-types"], queryFn: ({ signal }) => listAssetTypes(signal) });
  const complianceQ = useQuery({ queryKey: ["compliance", slug], queryFn: ({ signal }) => getBrandCompliance(slug, signal) });
  const activityQ = useQuery({ queryKey: ["activity", slug], queryFn: ({ signal }) => listActivity({ brand: slug, limit: 8 }, signal) });
  const teamQ = useQuery({ queryKey: ["agent-team", slug], queryFn: ({ signal }) => getAgentTeam(slug, signal) });

  if (brandQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  const brand = brandQ.data;
  if (!brand) {
    return <EmptyState title="Brand not found" description="The requested brand record was not returned by the API." />;
  }

  const assets = assetsFor(assetsQ.data ?? [], slug);
  const types = typesQ.data ?? [];
  const coverage = coverageFor(assets, types);
  const flagged = flaggedCount(assets);
  const score = complianceScore(complianceQ.data) ?? gradeFromCoverage(coverage.pct, flagged).score;
  const grade = gradeFromCoverage(score, flagged);
  const colors = brand.visual.colors;
  const palette = tokensQ.data?.color.length
    ? tokensQ.data.color
    : [
        { name: "background", value: colors.background || "#07070b", rgb: [] },
        { name: "primary", value: colors.primary, rgb: [] },
        { name: "accent", value: colors.accent, rgb: [] },
        { name: "foreground", value: colors.foreground || "#f2f1ee", rgb: [] },
        { name: "muted", value: colors.muted || "#26262f", rgb: [] },
      ];
  const note = fontNote(brand);
  const byType = new Map(assets.map((asset) => [asset.asset_type, asset]));
  const required = types.length ? types.map((type) => type.key) : Object.keys(REQUIRED_LABELS);
  const activity = activityQ.data ?? [];
  const team = teamQ.data;

  return (
    <>
      <section className="hq-hero">
        <div className="rise">
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 26 }}>
            <span className="microlabel">Brand HQ</span>
            {brand.parent && <span className="chip">Part of {brand.parent}</span>}
            <span className="chip">Steward · active</span>
          </div>
          <h1>{brand.display_name}</h1>
          <p className="hq-tagline">{brand.tagline || "Brand system loaded from the graph."}</p>
          <p className="hq-desc">{brand.description || "This brand record has tokens, voice, assets, and compliance data attached."}</p>
          <div className="hero-actions">
            <Link className="btn btn-accent" to={`/b/${slug}/studio`}>
              Open Studio
            </Link>
            <Link className="btn btn-ghost" to={`/b/${slug}/book`}>
              Brand book
            </Link>
            <Link className="btn btn-ghost" to={`/b/${slug}/studio?context=overview`}>
              Discuss the brand
            </Link>
          </div>
          {note && <p className="spec-note" style={{ marginTop: 18, display: "inline-flex" }}>{note}</p>}
        </div>
        <div className="hero-side rise d2">
          <div className="hero-mark aura">
            {brand.mark_url && <img src={brand.mark_url} alt={`${brand.display_name} mark`} />}
          </div>
          <div className="gradeboard">
            <div>
              <div className={`big ${grade.className}`}>{grade.letter}</div>
              <div className="microlabel">Brand grade</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 12, color: "var(--b-ink-2)" }}>
                {score} / 100 · computed from live data
              </span>
              <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 12, color: flagged ? "var(--c-warn)" : "var(--c-go)" }}>
                {flagged} open review items
              </span>
              <Link to={`/b/${slug}/grade`} style={{ color: "var(--b-accent)", fontSize: 12, textDecoration: "none" }}>
                Full report →
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>Color</h2>
          <span className="annotation">Live tokens from the brand API.</span>
          <Link className="btn btn-ghost discuss" to={`/b/${slug}/studio?context=color`}>
            Discuss ↗
          </Link>
        </div>
        <div className="paint-wall rise d1">
          {palette.slice(0, 5).map((token) => (
            <div key={token.name} className="paint-field" style={{ background: token.value, color: textColor(token.value) }}>
              <span className="ct">token/{token.name}</span>
              <span className="nm">{token.name}</span>
              <span className="hx">{token.value}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>Typography</h2>
          <span className="annotation">Declared families with fallback when not bundled.</span>
          <Link className="btn btn-ghost discuss" to={`/b/${slug}/studio?context=type`}>
            Discuss ↗
          </Link>
        </div>
        <div className="type-grid rise d1">
          <div className="specimen">
            <span className="microlabel">Display · {brand.visual.typography.display_font}</span>
            <div className="ag" style={{ fontFamily: "var(--b-font-display)" }}>Aa</div>
            <div style={{ color: "var(--b-ink-2)", overflow: "hidden", whiteSpace: "nowrap" }}>
              ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789
            </div>
          </div>
          <div className="specimen">
            <span className="microlabel">Body · {brand.visual.typography.primary_font}</span>
            <div className="ag" style={{ fontFamily: "var(--b-font-body)", fontSize: 64 }}>Ag</div>
            <p style={{ color: "var(--b-ink-2)" }}>{brand.tagline || "The quick brown fox surfaces the right signal."}</p>
            <p style={{ fontFamily: "var(--c-font-mono)", color: "var(--b-ink-2)" }}>{slug} --tokens live</p>
          </div>
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>The mark</h2>
          <span className="annotation">Canonical mark from the brand record.</span>
          <Link className="btn btn-ghost discuss" to={`/b/${slug}/mark`}>
            Full anatomy →
          </Link>
        </div>
        <div className="mark-row rise d1">
          <div className="mark-cell">
            <div className="mat">{brand.mark_url && <img src={brand.mark_url} alt="mark on light" />}</div>
            <span className="microlabel">On light</span>
          </div>
          <div className="mark-cell">
            <div className="mat dark">{brand.mark_url && <img src={brand.mark_url} alt="mark on dark" />}</div>
            <span className="microlabel">On dark</span>
          </div>
          <div className="mark-cell">
            <div className="mat dark" style={{ border: "1px dashed var(--b-accent)" }}>
              {brand.mark_url && <img src={brand.mark_url} alt="clear space" />}
            </div>
            <span className="microlabel">Clear-space preview</span>
          </div>
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>The kit</h2>
          <span className="annotation">Coverage counts approved or legacy ungated assets only.</span>
        </div>
        <div className="kit rise d1">
          {required.slice(0, 6).map((key) => {
            const asset = byType.get(key);
            return (
              <div key={key} className={`kit-cell ${asset ? "" : "missing"}`}>
                <div className="thumb">{asset?.url ? <img src={asset.url} alt="" /> : "＋"}</div>
                <span style={{ fontSize: 11.5, fontWeight: 600 }}>{REQUIRED_LABELS[key] || key}</span>
                {asset ? (
                  <span style={{ fontSize: 10.5, color: "var(--b-ink-3)" }}>{asset.lifecycle_status || "catalogued"}</span>
                ) : (
                  <Link to={`/b/${slug}/studio?asset=${key}`} style={{ fontSize: 11, fontWeight: 600, color: "var(--b-accent)" }}>
                    Generate →
                  </Link>
                )}
              </div>
            );
          })}
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>Voice</h2>
          <span className="annotation">Tone and banned phrases from the brand record.</span>
          <Link className="btn btn-ghost discuss" to={`/b/${slug}/studio?context=voice`}>
            Discuss ↗
          </Link>
        </div>
        <div className="voice-grid rise d1">
          <div className="voice-card">
            <span className="microlabel">Tone</span>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
              {brand.voice.tone.map((tone) => <span className="chip" key={tone}>{tone}</span>)}
            </div>
          </div>
          <div className="voice-card">
            <span className="microlabel">Never says</span>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 12 }}>
              {brand.voice.forbidden_phrases.map((phrase) => <s key={phrase} style={{ color: "var(--b-ink-3)", textDecorationColor: "var(--c-stop)" }}>{phrase}</s>)}
            </div>
          </div>
        </div>
      </section>

      <section className="block">
        <div className="sec-head rise">
          <h2>The team on this brand</h2>
          <span className="annotation">Agents from the registry, steward scoped to this brand.</span>
        </div>
        <div className="team rise d1">
          <div className="agent-card">
            <b>{team?.steward.name ?? `${brand.display_name} Steward`}</b>
            <p style={{ color: "var(--b-ink-3)" }}>{team?.steward.role ?? "Watching this brand."}</p>
          </div>
          <div className="agent-card">
            <b>{team?.director.name ?? "Iris"}</b>
            <p style={{ color: "var(--b-ink-3)" }}>{team?.director.role ?? "Director for cross-brand work and intake."}</p>
          </div>
          <div className="agent-card">
            <b>{team?.agents.length ?? 0} specialists</b>
            <p style={{ color: "var(--b-ink-3)" }}>{team?.agents.slice(0, 4).map((agent) => agent.name).join(" · ") || "No specialists reported."}</p>
          </div>
        </div>
      </section>

      <section className="block" style={{ paddingBottom: 64 }}>
        <div className="sec-head rise">
          <h2>Recent on this brand</h2>
        </div>
        <div className="act rise d1">
          {activity.length === 0 ? (
            <p style={{ margin: 0, color: "var(--b-ink-3)" }}>No scoped activity yet.</p>
          ) : (
            activity.map((event) => (
              <div className="act-row" key={event.id}>
                <span className={event.actor_kind === "system" ? "stamp stamp-flag" : "stamp stamp-pass"}>
                  {event.actor_kind}
                </span>
                <span style={{ fontWeight: 600 }}>{event.actor}</span>
                <span style={{ color: "var(--b-ink-2)" }}>{displayActivity(event)}</span>
                <span style={{ marginLeft: "auto", fontFamily: "var(--c-font-mono)", fontSize: 10.5, color: "var(--b-ink-3)" }}>
                  {relativeTime(event.ts)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>
    </>
  );
}
