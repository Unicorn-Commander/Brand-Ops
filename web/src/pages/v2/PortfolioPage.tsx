import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listActivity, listAssets, listAssetTypes, listBrands } from "../../api/client";
import type { AssetRecord, AssetType, BrandDetail, BrandSummary } from "../../lib/types";
import {
  assetsFor,
  brandColors,
  coverageFor,
  displayActivity,
  flaggedCount,
  gradeFromCoverage,
  relativeTime,
} from "../../v2/metrics";

const builtInFonts: Record<string, string> = {
  Inter: "InterVariable, Inter, system-ui, sans-serif",
  "Space Grotesk": '"Space Grotesk Variable", "Space Grotesk", InterVariable, system-ui, sans-serif',
  "Plus Jakarta Sans": '"Plus Jakarta Sans", InterVariable, system-ui, sans-serif',
};

function Cover({
  brand,
  assets,
  types,
  index,
}: {
  brand: BrandSummary & Partial<BrandDetail>;
  assets: AssetRecord[];
  types: AssetType[];
  index: number;
}) {
  const colors = brandColors(brand);
  const coverage = coverageFor(assets, types);
  const flagged = flaggedCount(assets);
  const grade = gradeFromCoverage(coverage.pct, flagged);
  const font = brand.visual?.typography.display_font
    ? builtInFonts[brand.visual.typography.display_font] ?? brand.visual.typography.display_font
    : "var(--c-font-display)";
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
          <span className="parent-chip">{brand.parent ? "Part of house" : "Root"}</span>
          <span className="cover-grade">
            <b className={grade.className}>{grade.letter}</b>
            <span>grade</span>
          </span>
        </div>
        <div className="cover-mark">
          {brand.mark_url ? <img src={brand.mark_url} alt={`${brand.display_name} mark`} /> : null}
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
  const activityQ = useQuery({
    queryKey: ["activity", "portfolio"],
    queryFn: ({ signal }) => listActivity({ limit: 20 }, signal),
  });

  const brands = brandsQ.data ?? [];
  const assets = assetsQ.data ?? [];
  const types = typesQ.data ?? [];
  const activity = activityQ.data ?? [];

  const stats = useMemo(() => {
    const needsReview = assets.filter((asset) => asset.lifecycle_status === "needs_review").length;
    return {
      assets: assets.length,
      review: needsReview,
      flaggedToday: activity.filter((event) => /flag/i.test(event.action)).length,
    };
  }, [activity, assets]);

  function submitCommand() {
    const params = new URLSearchParams({ prompt: command });
    navigate(`/b/${brands[0]?.slug ?? "magic-unicorn"}/studio?${params.toString()}`);
  }

  const ticker = activity.length ? activity : [];
  const roots = brands.filter((brand) => !brand.parent);
  const children = brands.filter((brand) => brand.parent);

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
              <span>flagged feed</span>
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
            aria-label="Director command"
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
          <Link className="chip" to="/standards">
            Audit the portfolio
          </Link>
          <Link className="chip" to="/library">
            Open Library
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

      <section className="brand-shelf">
        {brands.map((brand, index) => (
          <Cover
            key={brand.slug}
            brand={brand}
            assets={assetsFor(assets, brand.slug)}
            types={types}
            index={index}
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
              {brand.mark_url && <img src={brand.mark_url} alt="" style={{ width: 30, height: 30, objectFit: "contain", borderRadius: 8 }} />}
              <b>{brand.display_name}</b>
              <span style={{ color: "var(--c-ink-3)", fontSize: 11 }}>{brand.parent ? `Part of ${brand.parent}` : "Root"}</span>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
