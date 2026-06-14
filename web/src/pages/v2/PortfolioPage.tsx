import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getAgentTeam, getBrand, getBrandCompliance, listActivity, listAgents, listAssets, listAssetTypes, listBrands } from "../../api/client";
import type { AssetRecord, AssetType, BrandComplianceRollup, BrandDetail, BrandSummary } from "../../lib/types";
import { MarkImg } from "../../components/MarkImg";
import FrontDoor from "../../v2/FrontDoor";
import {
  assetsFor,
  brandColors,
  brandGrade,
  coverageFor,
  displayActivity,
  flaggedCount,
  relativeTime,
} from "../../v2/metrics";
import { fontStack } from "../../v2/EnvProvider";

function Cover({
  brand,
  assets,
  types,
  rollup,
  index,
  parentName,
}: {
  brand: BrandSummary & Partial<BrandDetail>;
  assets: AssetRecord[];
  types: AssetType[];
  rollup: BrandComplianceRollup | undefined;
  index: number;
  parentName?: string;
}) {
  const colors = brandColors(brand);
  const coverage = coverageFor(assets, types);
  const flagged = flaggedCount(assets);
  const grade = brandGrade(assets, types, rollup);
  const font = brand.visual?.typography.display_font ? fontStack(brand.visual.typography.display_font) : "var(--c-font-display)";
  return (
    <Link
      className={`brand-cover rise d${Math.min(index + 2, 6)}`}
      to={`/b/${brand.slug}/overview`}
      style={
        {
          "--bg": colors.bg,
          "--g1": colors.primary,
          "--g2": colors.accent,
          "--ink": colors.ink,
          "--ink-2": "rgba(255,255,255,0.66)",
          "--ink-3": "rgba(255,255,255,0.44)",
          "--ink-3a": "rgba(255,255,255,0.22)",
          "--glowc": colors.accent,
          "--brand-font": font,
        } as CSSProperties
      }
    >
      <div className="env" />
      <div className="inner">
        <div className="cover-top">
          <span className="parent-chip">{brand.parent ? `Part of ${parentName ?? brand.parent}` : "Root"}</span>
          <span className="cover-grade">
            <b className={grade.className}>{grade.letter}</b>
            <span>grade</span>
          </span>
        </div>
        <div className="cover-mark">
          <MarkImg brand={brand} />
        </div>
        <h2 className="cover-name">{brand.display_name}</h2>
        <p className="cover-tag">{brand.tagline || "Brand record loaded from the system."}</p>
        <div className="cover-meta">
          <span>{assets.length} assets</span>
          <span>·</span>
          <span>
            {coverage.present} of {coverage.total} types covered
          </span>
          {flagged > 0 && (
            <>
              <span>·</span>
              <span style={{ color: "var(--c-warn)" }}>{flagged} in review</span>
            </>
          )}
        </div>
      </div>
      <div className="paint">
        {[colors.primary, colors.accent, colors.bg, colors.ink, colors.muted].map((color) => (
          <span key={color} style={{ background: color }} />
        ))}
      </div>
    </Link>
  );
}

export default function PortfolioPage() {
  const navigate = useNavigate();
  const [command, setCommand] = useState("Stand up a brand for the Crisis-Ops launch");
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const assetsQ = useQuery({ queryKey: ["assets", "all"], queryFn: ({ signal }) => listAssets({}, signal) });
  const typesQ = useQuery({ queryKey: ["asset-types"], queryFn: ({ signal }) => listAssetTypes(signal) });
  const agentsQ = useQuery({ queryKey: ["agents"], queryFn: ({ signal }) => listAgents(signal) });
  const activityQ = useQuery({
    queryKey: ["activity", "portfolio"],
    queryFn: ({ signal }) => listActivity({ limit: 20 }, signal),
  });

  const brands = brandsQ.data ?? [];
  const brandDetailQs = useQueries({
    queries: brands.map((brand) => ({
      queryKey: ["brand", brand.slug],
      queryFn: ({ signal }: { signal: AbortSignal }) => getBrand(brand.slug, signal),
      staleTime: 60_000,
    })),
  });
  const complianceQs = useQueries({
    queries: brands.map((brand) => ({
      queryKey: ["compliance", brand.slug],
      queryFn: ({ signal }: { signal: AbortSignal }) => getBrandCompliance(brand.slug, signal),
      staleTime: 60_000,
    })),
  });
  const complianceBySlug = new Map(brands.map((brand, index) => [brand.slug, complianceQs[index]?.data]));
  const detailedBrands = brands.map((brand, index) => brandDetailQs[index]?.data ?? brand);
  const brandNameBySlug = new Map(detailedBrands.map((brand) => [brand.slug, brand.display_name]));
  const teamQs = useQueries({
    queries: brands.map((brand) => ({
      queryKey: ["agent-team", brand.slug],
      queryFn: ({ signal }: { signal: AbortSignal }) => getAgentTeam(brand.slug, signal),
      staleTime: 60_000,
    })),
  });
  const assets = assetsQ.data ?? [];
  const types = typesQ.data ?? [];
  const agents = agentsQ.data ?? [];
  const teams = teamQs.map((query) => query.data).filter(Boolean);
  const activity = activityQ.data ?? [];

  const stats = useMemo(() => {
    const needsReview = assets.filter((asset) => asset.lifecycle_status === "needs_review").length;
    const cutoff = Date.now() - 24 * 60 * 60 * 1000;
    return {
      assets: assets.length,
      review: needsReview,
      flaggedToday: activity.filter((event) => /flag/i.test(event.action) && new Date(event.ts).getTime() >= cutoff).length,
    };
  }, [activity, assets]);

  // First-run front door: a brand-new workspace (zero brands) should never meet
  // the power-user command deck. Once the brands query has settled and there are
  // none, hand the newcomer a goal-first start instead of "No brands, standing
  // by". Placed after every hook above has run (the downstream queries over an
  // empty brand list are no-ops) so the rules of hooks hold as the list fills.
  if (!brandsQ.isLoading && brands.length === 0) {
    return <FrontDoor />;
  }

  function submitCommand() {
    const params = new URLSearchParams({ prompt: command });
    navigate(`/b/${brands[0]?.slug ?? "magic-unicorn"}/studio?${params.toString()}`);
  }

  const ticker = activity.length ? activity : [];
  const currentActivity = activity[0];
  const pendingIntake = activity.filter((event) => /intake|draft|propos/i.test(`${event.action} ${event.note ?? ""}`)).length;
  const signOffCount = stats.review + pendingIntake;
  const reviewItems = assets.filter((asset) => asset.lifecycle_status === "needs_review").slice(0, 2);
  const createdAgentCount = teams.reduce((sum, team) => sum + (team?.governance.total ?? 0), 0);
  const onDuty = agents.length + teams.length + createdAgentCount;
  const roots = detailedBrands.filter((brand) => !brand.parent);
  const children = detailedBrands.filter((brand) => brand.parent);

  return (
    <>
      <section className="v2-head rise">
        <div>
          <span className="microlabel">Workspace · Magic Unicorn · {brands.length} brands</span>
          <h1>
            {brands.length || "No"} brands,
            <br />
            <span className="prism-text">standing by.</span>
          </h1>
          <p className="annotation">Machine-readable identity, agents on watch. Say the word.</p>
        </div>
        <div className="v2-head-right">
          <div className="statline">
            <div>
              <b>{brands.length}</b>
              <span>brands</span>
            </div>
            <div>
              <b>{stats.assets}</b>
              <span>production assets</span>
            </div>
            <div>
              <b>{stats.review}</b>
              <span>in review</span>
            </div>
            <div className="flag">
              <b>{stats.flaggedToday}</b>
              <span>flagged today</span>
            </div>
          </div>
        </div>
      </section>

      <section className="rise d1" style={{ marginTop: 26 }}>
        <form
          className="omnibar"
          onSubmit={(event) => {
            event.preventDefault();
            submitCommand();
          }}
        >
          <span className="orb">I</span>
          <input
            aria-label="Tell Iris what to make"
            placeholder="Tell Iris what to make, or ask a question..."
            value={command}
            onChange={(event) => setCommand(event.target.value)}
          />
          <button className="btn btn-ghost" type="submit">
            Studio
          </button>
        </form>
        <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
          <span className="microlabel">Or jump</span>
          <Link className="chip" to="/brands/new">
            New brand
          </Link>
          <Link className="chip" to={`/b/${brands[0]?.slug ?? "magic-unicorn"}/studio?pack=core`}>
            Generate a kit
          </Link>
          <Link className="chip" to="/standards">
            Audit the portfolio
          </Link>
          <Link className="chip" to={`/b/${brands[0]?.slug ?? "magic-unicorn"}/book`}>
            Draft a book
          </Link>
        </div>
      </section>

      <div className="rise d2" style={{ margin: "26px 0 6px" }}>
        <div className="ticker">
          <div className="ticker-track">
            {[...ticker, ...ticker].slice(0, Math.max(8, ticker.length * 2)).map((event, index) => (
              <span className="tick-item" key={`${event.id}-${index}`}>
                <span className="dot" style={{ background: event.actor_kind === "system" ? "var(--c-warn)" : "var(--c-go)" }} />
                <b>{event.actor}</b> {displayActivity(event)} · {relativeTime(event.ts)}
              </span>
            ))}
          </div>
        </div>
      </div>

      <section className="rise d3 duty-grid" style={{ display: "grid", gap: 18, marginTop: 20 }}>
        <div className="voice-card" style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 18px" }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <span className="orb" style={{ width: 32, height: 32, fontSize: 13, outline: "2.5px solid var(--c-surface)" }}>I</span>
            {detailedBrands.slice(0, 6).map((brand) => (
              <span
                key={brand.slug}
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 999,
                  display: "grid",
                  placeItems: "center",
                  overflow: "hidden",
                  marginLeft: -9,
                  outline: "2.5px solid var(--c-surface)",
                  background: "var(--c-bg-2)",
                }}
              >
                <MarkImg brand={brand} style={{ borderRadius: 999, fontSize: 13 }} />
              </span>
            ))}
            {agents.length > 0 && (
              <span
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: 999,
                  display: "grid",
                  placeItems: "center",
                  marginLeft: -9,
                  outline: "2.5px solid var(--c-surface)",
                  background: "var(--c-bg-2)",
                  color: "var(--c-ink-3)",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              >
                +{agents.length}
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "var(--c-ink-2)", lineHeight: 1.5 }}>
            <b style={{ color: "var(--c-ink)" }}>On duty · {onDuty}</b>
            {currentActivity ? (
              <> — {displayActivity(currentActivity)} · {relativeTime(currentActivity.ts)}</>
            ) : (
              " — no activity yet"
            )}
          </div>
          <Link to="/crew" style={{ marginLeft: "auto", fontSize: 11.5, fontWeight: 600, color: "var(--c-ink)", textDecoration: "none", whiteSpace: "nowrap" }}>
            Crew →
          </Link>
        </div>
        <div
          style={{
            border: "1px solid rgba(232,181,74,0.35)",
            borderRadius: "var(--radius)",
            background: "var(--c-surface)",
            boxShadow: "var(--shadow-paper), 0 0 36px -14px rgba(232,181,74,0.3)",
            padding: "12px 18px",
            display: "flex",
            alignItems: "center",
            gap: 14,
          }}
        >
          <div>
            <b style={{ fontFamily: "var(--c-font-display)", fontSize: 14, display: "block" }}>Your sign-off · {signOffCount}</b>
            <span style={{ fontSize: 10.5, color: "var(--c-ink-3)" }}>agents propose, you decide</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1, minWidth: 0 }}>
            {reviewItems.map((asset) => (
              <Link key={asset.file} to="/library?status=needs_review" style={{ fontSize: 11, color: "var(--c-ink-2)", textDecoration: "none", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                <b style={{ color: "var(--c-ink)" }}>{brandNameBySlug.get(asset.brand) ?? asset.brand}</b> · {asset.asset_type}/{asset.variant ?? asset.file}
              </Link>
            ))}
            {pendingIntake > 0 && (
              <Link to="/brands/new" style={{ fontSize: 11, color: "var(--c-ink-2)", textDecoration: "none" }}>
                <b style={{ color: "var(--c-ink)" }}>Iris</b> · {pendingIntake} intake draft{pendingIntake === 1 ? "" : "s"}
              </Link>
            )}
            {signOffCount === 0 && <div style={{ fontSize: 11, color: "var(--c-ink-3)" }}>No review items or intake drafts are pending.</div>}
          </div>
          <Link className="btn btn-ink" to={stats.review > 0 ? "/library?status=needs_review" : "/brands/new"} style={{ fontSize: 12, padding: "8px 14px", whiteSpace: "nowrap" }}>
            Review
          </Link>
        </div>
      </section>

      <section className="brand-shelf">
        {detailedBrands.map((brand, index) => (
          <Cover
            key={brand.slug}
            brand={brand}
            assets={assetsFor(assets, brand.slug)}
            types={types}
            rollup={complianceBySlug.get(brand.slug)}
            index={index}
            parentName={brand.parent ? brandNameBySlug.get(brand.parent) : undefined}
          />
        ))}
        <Link className="brand-cover ghost rise d6" to="/brands/new">
          <div className="ghost-inner">
            <div style={{ fontSize: 30, color: "var(--c-ink-3)" }}>＋</div>
            <div>
              <h3 style={{ margin: 0, color: "var(--c-ink)" }}>New brand</h3>
              <p style={{ margin: 0, color: "var(--c-ink-2)", fontSize: 12.5 }}>
                The Director researches, proposes an identity, and stands up the steward.
              </p>
            </div>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
              <span className="chip">Extend the house</span>
              <span className="chip">From a brand</span>
              <span className="chip">From nothing</span>
            </div>
          </div>
        </Link>
      </section>

      <section className="rise d4" style={{ margin: "44px 0 60px" }}>
        <span className="microlabel">Brand architecture</span>
        <div style={{ marginTop: 18, border: "1px solid var(--c-hairline-soft)", background: "var(--c-surface)", borderRadius: "var(--radius)", padding: "26px 30px", boxShadow: "var(--shadow-paper)" }}>
          {[...roots, ...children].map((brand) => (
            <div key={brand.slug} style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 0 8px", marginLeft: brand.parent ? 34 : 0 }}>
              <MarkImg brand={brand} style={{ width: 30, height: 30, borderRadius: 8 }} imgStyle={{ width: 30, height: 30, objectFit: "contain", borderRadius: 8 }} />
              <b>{brand.display_name}</b>
              <span style={{ color: "var(--c-ink-3)", fontSize: 11 }}>{brand.parent ? `Part of ${brandNameBySlug.get(brand.parent) ?? brand.parent}` : "Root"}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
