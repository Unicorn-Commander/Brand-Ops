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
  listBrands,
} from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { MarkImg } from "../../components/MarkImg";
import { Skeleton } from "../../components/Skeleton";
import { fontNote } from "../../v2/EnvProvider";
import {
  assetsFor,
  brandGrade,
  coverageFor,
  displayActivity,
  flaggedCount,
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

// Layout-matched loading state for the brand/tokens/compliance queries: mirrors
// the hero (mark + grade), the color-token wall, and the kit-coverage list, so
// the load reads ~20-30% faster than a bare spinner and the real blocks land
// into reserved slots. The skeleton itself shimmers (the `.skeleton` class,
// self-stilling under reduced-motion) — no rise/animate-in.
function BrandHQSkeleton() {
  return (
    <>
      <section className="hq-hero">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 26 }}>
            <Skeleton width={90} height={12} />
            <Skeleton width={110} height={20} radius={999} />
          </div>
          <Skeleton width="62%" height={52} radius={10} />
          <Skeleton width="80%" height={16} style={{ marginTop: 18 }} />
          <Skeleton width="92%" height={13} style={{ marginTop: 12 }} />
          <Skeleton width="74%" height={13} style={{ marginTop: 6 }} />
          <div style={{ display: "flex", gap: 10, marginTop: 26 }}>
            <Skeleton width={120} height={40} radius={10} />
            <Skeleton width={110} height={40} radius={10} />
            <Skeleton width={140} height={40} radius={10} />
          </div>
        </div>
        <div className="hero-side">
          <div style={{ display: "grid", placeItems: "center", padding: "20px 0 36px" }}>
            <Skeleton width={180} height={180} radius={999} />
          </div>
          <div className="gradeboard">
            <Skeleton width={56} height={48} radius={10} />
            <div style={{ display: "flex", flexDirection: "column", gap: 8, flex: 1 }}>
              <Skeleton width="80%" height={12} />
              <Skeleton width="55%" height={12} />
              <Skeleton width={90} height={12} />
            </div>
          </div>
        </div>
      </section>

      <section className="hq-section">
        <div className="sec-head">
          <Skeleton width={120} height={26} radius={6} />
        </div>
        <div className="paint-wall">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} height="100%" radius="var(--radius-sm)" style={{ minHeight: 86 }} />
          ))}
        </div>
      </section>

      <section className="hq-section">
        <div className="sec-head">
          <Skeleton width={90} height={26} radius={6} />
        </div>
        <div className="kit">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="kit-cell">
              <Skeleton height={86} radius={7} />
              <Skeleton width="70%" height={11} />
              <Skeleton width="45%" height={10} />
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

export default function BrandHQPage() {
  const { slug = "" } = useParams();
  const brandQ = useQuery({ queryKey: ["brand", slug], queryFn: ({ signal }) => getBrand(slug, signal) });
  const tokensQ = useQuery({ queryKey: ["brand-tokens", slug], queryFn: ({ signal }) => getBrandTokens(slug, signal) });
  const assetsQ = useQuery({ queryKey: ["assets", slug], queryFn: ({ signal }) => listAssets({ brand: slug }, signal) });
  const typesQ = useQuery({ queryKey: ["asset-types"], queryFn: ({ signal }) => listAssetTypes(signal) });
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const complianceQ = useQuery({ queryKey: ["compliance", slug], queryFn: ({ signal }) => getBrandCompliance(slug, signal) });
  const activityQ = useQuery({ queryKey: ["activity", slug], queryFn: ({ signal }) => listActivity({ brand: slug, limit: 8 }, signal) });
  const teamQ = useQuery({ queryKey: ["agent-team", slug], queryFn: ({ signal }) => getAgentTeam(slug, signal) });

  // Replace the bare centered spinner with a layout-matched skeleton while the
  // brand record loads (the hero, tokens, and compliance below all depend on
  // it). The not-found check stays distinct: loaded-but-absent → EmptyState.
  if (brandQ.isLoading) {
    return <BrandHQSkeleton />;
  }

  const brand = brandQ.data;
  if (!brand) {
    return <EmptyState title="Brand not found" description="The requested brand record was not returned by the API." />;
  }

  const assets = assetsFor(assetsQ.data ?? [], slug);
  const types = typesQ.data ?? [];
  const coverage = coverageFor(assets, types);
  const flagged = flaggedCount(assets);
  const g = brandGrade(assets, types, complianceQ.data);
  const grade = g;
  const score = g.score;
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
  const parentName = brand.parent ? brandsQ.data?.find((item) => item.slug === brand.parent)?.display_name ?? brand.parent : null;

  const invalidRefs = brand.invalid_references ?? [];
  const missingRefs = brand.missing_references ?? [];
  const refPaths = brand.reference_paths ?? {};

  return (
    <>
      {(invalidRefs.length > 0 || missingRefs.length > 0) && (
        <section
          role="alert"
          className="hq-section rise"
          style={{
            marginBottom: 18,
            border: `1px solid ${invalidRefs.length ? "rgba(248,113,113,0.45)" : "rgba(251,191,36,0.45)"}`,
            background: invalidRefs.length ? "rgba(248,113,113,0.08)" : "rgba(251,191,36,0.08)",
            borderRadius: 14,
            padding: "14px 18px",
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
            {invalidRefs.length > 0
              ? "Reference paths misconfigured"
              : "Reference files missing"}
          </div>
          <p style={{ margin: 0, fontSize: 12.5, color: "var(--b-ink-2)", lineHeight: 1.45 }}>
            {invalidRefs.length > 0 && (
              <>
                Absolute host paths or kit escapes are never used for generation:{" "}
                <code style={{ fontFamily: "var(--c-font-mono)" }}>
                  {invalidRefs.map((r) => `${r}${refPaths[r] ? `→${refPaths[r]}` : ""}`).join("; ")}
                </code>
                . Use kit-relative paths only (e.g.{" "}
                <code style={{ fontFamily: "var(--c-font-mono)" }}>imported/logo/mark.png</code>
                ).
                {missingRefs.length > 0 ? " " : ""}
              </>
            )}
            {missingRefs.length > 0 && (
              <>
                Declared but not found on disk:{" "}
                <code style={{ fontFamily: "var(--c-font-mono)" }}>
                  {missingRefs.map((r) => `${r}${refPaths[r] ? `→${refPaths[r]}` : ""}`).join("; ")}
                </code>
                .
              </>
            )}
          </p>
        </section>
      )}

      <section className="hq-hero">
        <div className="rise">
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 26 }}>
            <span className="microlabel">Brand HQ</span>
            {parentName && <span className="chip">Part of {parentName}</span>}
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
          <div className="hero-mark aura" style={{ position: "relative" }}>
            <span style={{ position: "absolute", inset: -18, border: "1px solid color-mix(in srgb, var(--b-accent) 35%, transparent)", borderRadius: 999, boxShadow: "0 0 54px color-mix(in srgb, var(--b-accent) 20%, transparent)" }} />
            {[0, 1, 2, 3, 4, 5, 6, 7].map((tick) => (
              <span
                key={tick}
                style={{
                  position: "absolute",
                  left: "50%",
                  top: "50%",
                  width: 2,
                  height: 12,
                  background: "var(--b-accent)",
                  opacity: 0.45,
                  transform: `rotate(${tick * 45}deg) translateY(-146px)`,
                  transformOrigin: "0 146px",
                }}
              />
            ))}
            <MarkImg brand={brand} />
            <span style={{ position: "absolute", right: -10, bottom: 12, fontFamily: "var(--c-font-mono)", fontSize: 10, color: "var(--b-ink-3)", background: "rgba(0,0,0,0.38)", border: "1px solid var(--b-hairline)", borderRadius: 8, padding: "5px 7px" }}>
              {coverage.present}/{coverage.total} kit · {flagged} flags
            </span>
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

      <section className="hq-section">
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

      <section className="hq-section">
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

      {brand.visual.mascot_style && (
        <section className="hq-section">
          <div className="sec-head rise">
            <h2>Mascot style</h2>
            <span className="annotation">Agent-avatar character recipe (refs/ preferred over logos).</span>
          </div>
          <p className="rise d1" style={{ margin: 0, maxWidth: 720, color: "var(--b-ink-2)", lineHeight: 1.5, fontSize: 13.5 }}>
            {brand.visual.mascot_style}
          </p>
        </section>
      )}

      <section className="hq-section">
        <div className="sec-head rise">
          <h2>The mark</h2>
          <span className="annotation">Canonical mark from the brand record.</span>
          <Link className="btn btn-ghost discuss" to={`/b/${slug}/mark`}>
            Full anatomy →
          </Link>
        </div>
        <div className="mark-row rise d1">
          <div className="mark-cell">
            <div className="mat"><MarkImg brand={brand} /></div>
            <span className="microlabel">On light</span>
          </div>
          <div className="mark-cell">
            <div className="mat dark"><MarkImg brand={brand} /></div>
            <span className="microlabel">On dark</span>
          </div>
          <div className="mark-cell">
            <div className="mat dark" style={{ border: "1px dashed var(--b-accent)" }}>
              <MarkImg brand={brand} />
            </div>
            <span className="microlabel">Clear-space preview</span>
          </div>
        </div>
      </section>

      <section className="hq-section">
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

      <section className="hq-section">
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

      <section className="hq-section">
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

      <section className="hq-section" style={{ paddingBottom: 64 }}>
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
