import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getBrand, listAssets } from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { MarkImg } from "../../components/MarkImg";
import { Spinner } from "../../components/Spinner";
import type { AssetRecord } from "../../lib/types";
import { relativeTime } from "../../v2/metrics";

const CALLOUTS = [
  ["Signature form", "The primary silhouette that must remain recognizable."],
  ["House detail", "Inherited visual DNA from the parent or workspace system."],
  ["Role cue", "The mark should say what this brand does before copy explains it."],
  ["Production outline", "Keep edges readable across mats, crops, and thumbnails."],
  ["Accent moment", "A small controlled hit of the accent color, never random."],
  ["Clear field", "The mark needs air; busy backgrounds go to review."],
] as const;

export default function MarkPage() {
  const { slug = "" } = useParams();
  const brandQ = useQuery({ queryKey: ["brand", slug], queryFn: ({ signal }) => getBrand(slug, signal) });
  const assetsQ = useQuery({ queryKey: ["assets", slug], queryFn: ({ signal }) => listAssets({ brand: slug }, signal) });

  if (brandQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandQ.data) {
    return <EmptyState title="Brand not found" description="The mark surface needs a known brand slug." />;
  }

  const brand = brandQ.data;
  const assets = assetsQ.data ?? [];
  const markAssets = assets.filter((asset) => /logo|mark|icon|avatar|seal/i.test(asset.asset_type));
  const seal = assets.find((asset) => /seal/i.test(`${asset.asset_type} ${asset.variant ?? ""}`));
  const lineage = buildLineage(markAssets);
  const callouts = CALLOUTS.map(([fallbackTitle, body], index) => ({
    title: brand.visual.style_keywords[index] ?? fallbackTitle,
    body,
    side: index < 3 ? "left" : "right",
    top: [96, 230, 374, 110, 248, 380][index],
  }));

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 22, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">The mark · anatomy sheet</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 40, letterSpacing: "-0.025em" }}>
            {brand.display_name}
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            Canonical construction, usage, and lineage from the brand record and catalog.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn btn-ghost" to={`/b/${slug}/studio?context=mark`}>
            Ask why it looks this way
          </Link>
          <Link className="btn btn-accent" to={`/b/${slug}/studio?asset=logo`}>
            Commission variant
          </Link>
        </div>
      </div>

      <section
        style={{
          position: "relative",
          margin: "30px 0 14px",
          border: "1px solid var(--b-hairline)",
          borderRadius: "var(--radius)",
          background: "rgba(255,255,255,0.02)",
          padding: "60px 40px",
          display: "grid",
          placeItems: "center",
          minHeight: 540,
          overflow: "hidden",
        }}
      >
        <div style={{ position: "absolute", inset: 0, background: "linear-gradient(rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.045) 1px, transparent 1px)", backgroundSize: "44px 44px" }} />
        <span style={circle(470)} />
        <span style={circle(320)} />
        <div style={{ position: "relative", zIndex: 2, width: 400, height: 400, display: "grid", placeItems: "center", filter: "drop-shadow(0 24px 60px rgba(0,0,0,0.55))" }}>
          <MarkImg brand={brand} imgStyle={{ maxHeight: 360, maxWidth: 340, objectFit: "contain" }} style={{ width: 260, height: 260, fontSize: 120 }} />
        </div>
        {callouts.map((callout) => (
          <div
            key={`${callout.title}-${callout.top}`}
            style={{
              position: "absolute",
              zIndex: 3,
              maxWidth: 230,
              top: callout.top,
              ...(callout.side === "left" ? { left: 60, textAlign: "right" } : { right: 60 }),
            }}
          >
            <b style={{ fontFamily: "var(--b-font-display)", display: "block", fontSize: 13.5, marginBottom: 3 }}>{callout.title}</b>
            <p style={{ margin: 0, fontSize: 11.5, color: "var(--b-ink-3)", lineHeight: 1.45 }}>{callout.body}</p>
            <span style={{ position: "absolute", top: 9, width: 70, height: 1, background: "color-mix(in srgb, var(--b-accent) 60%, transparent)", ...(callout.side === "left" ? { right: -84 } : { left: -84 }) }} />
            <span style={{ position: "absolute", top: 6, width: 7, height: 7, borderRadius: 99, background: "var(--b-accent)", boxShadow: "0 0 0 4px color-mix(in srgb, var(--b-accent) 20%, transparent)", ...(callout.side === "left" ? { right: -94 } : { left: -94 }) }} />
          </div>
        ))}
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1.1fr 1fr 1.1fr", gap: 22, margin: "26px 0" }}>
        <Panel title="Clear space">
          <div style={{ position: "relative", display: "grid", placeItems: "center", height: 210 }}>
            <div style={{ position: "relative", border: "1px dashed color-mix(in srgb, var(--b-accent) 55%, transparent)", padding: 38, borderRadius: 8 }}>
              <MarkImg brand={brand} imgStyle={{ height: 92, width: "auto", objectFit: "contain" }} style={{ width: 92, height: 92 }} />
              {[
                { key: "top", pos: { top: 10, left: "50%", transform: "translateX(-50%)" } },
                { key: "bottom", pos: { bottom: 10, left: "50%", transform: "translateX(-50%)" } },
                { key: "left", pos: { left: 10, top: "50%", transform: "translateY(-50%)" } },
                { key: "right", pos: { right: 10, top: "50%", transform: "translateY(-50%)" } },
              ].map(({ key, pos }) => (
                <span key={key} style={{ position: "absolute", fontFamily: "var(--c-font-mono)", fontSize: 9.5, color: "var(--b-accent)", ...(pos as object) }}>1x</span>
              ))}
            </div>
          </div>
          <p style={smallText}>Keep at least 1x mark height clear on every side.</p>
        </Panel>

        <Panel title="Minimum size">
          <div style={{ display: "flex", alignItems: "flex-end", gap: 26, height: 210, padding: "0 8px 14px" }}>
            {[96, 48, 24, 14].map((size) => (
              <div key={size} style={{ textAlign: "center" }}>
                <MarkImg brand={brand} imgStyle={{ height: size, width: "auto", objectFit: "contain", opacity: size === 14 ? 0.55 : 1 }} style={{ width: size, height: size, opacity: size === 14 ? 0.55 : 1 }} />
                <span style={{ display: "block", marginTop: 8, fontFamily: "var(--c-font-mono)", fontSize: 10, color: size === 14 ? "#f49b8a" : "#8be3a8" }}>
                  {size}px {size === 14 ? "x" : "✓"}
                </span>
              </div>
            ))}
          </div>
          <p style={smallText}>Below 24px, use a simplified glyph or text lockup.</p>
        </Panel>

        <Panel title="Lineage">
          <div style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 210 }}>
            {lineage.map((item, index) => (
              <>
                <div key={item.label} style={{ textAlign: "center", flex: 1 }}>
                  <div style={{ height: 104, borderRadius: 10, background: "var(--mat-dark)", display: "grid", placeItems: "center", overflow: "hidden", boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.06)" }}>
                    {item.url ? <img src={item.url} alt="" style={{ maxHeight: 84, maxWidth: "88%", objectFit: "contain" }} /> : <MarkImg brand={brand} style={{ width: 84, height: 84 }} />}
                  </div>
                  <span style={{ display: "block", fontSize: 10.5, color: "var(--b-ink-3)", marginTop: 8, lineHeight: 1.4 }}>{item.label}</span>
                </div>
                {index < lineage.length - 1 && <span style={{ color: "var(--b-ink-3)", fontSize: 17 }}>→</span>}
              </>
            ))}
          </div>
          <p style={smallText}>Refs → concept → production where catalog sidecars expose it.</p>
        </Panel>
      </section>

      <span className="microlabel">Approved uses</span>
      <section style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 18, margin: "14px 0 26px" }}>
        <UseTile brand={brand} label="Primary · on brand field" bg="var(--b-bg)" />
        <UseTile brand={brand} label="On light mat" className="mat" />
        <UseTile brand={brand} label="Mono via CSS filter" bg="var(--mat-dark)" filter="grayscale(1) contrast(1.1)" />
        <UseTile brand={brand} label={seal ? "Ceremonial seal" : "Seal not in catalog"} bg="var(--b-bg)" sealUrl={seal?.url ?? null} />
      </section>

      <span className="microlabel">Do / don't</span>
      <section style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 18, margin: "14px 0 26px" }}>
        <DoTile brand={brand} ok label="Flat brand fields" />
        <DoTile brand={brand} label="Never stretch or skew" transform="scaleX(1.55)" />
        <DoTile brand={brand} label="Never recolor" filter="hue-rotate(120deg) saturate(1.6)" />
        <DoTile brand={brand} label="No busy fields" busy />
      </section>
    </div>
  );
}

function buildLineage(markAssets: AssetRecord[]) {
  const production = markAssets.find((asset) => asset.lifecycle_status === "approved") ?? markAssets[0];
  const concept = markAssets.find((asset) => asset !== production) ?? production;
  const ref = production?.reference_images[0] ?? concept?.reference_images[0] ?? null;
  return [
    { label: ref ? "reference image" : "refs not catalogued", url: ref },
    { label: concept ? `concept · ${concept.asset_type}` : "concept not catalogued", url: concept?.url ?? null },
    { label: production ? `production · ${relativeTime(production.created_at)}` : "production mark fallback", url: production?.url ?? null },
  ];
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="voice-card" style={{ padding: "22px 26px" }}>
      <h3 style={{ margin: "0 0 14px", fontFamily: "var(--b-font-display)", fontSize: 15 }}>{title}</h3>
      {children}
    </div>
  );
}

function UseTile({ brand, label, bg, className, filter, sealUrl }: { brand: Parameters<typeof MarkImg>[0]["brand"]; label: string; bg?: string; className?: string; filter?: string; sealUrl?: string | null }) {
  return (
    <div>
      <div className={className} style={{ height: 180, borderRadius: "var(--radius-sm)", display: "grid", placeItems: "center", overflow: "hidden", background: bg }}>
        {sealUrl ? <img src={sealUrl} alt="" style={{ maxHeight: 130, maxWidth: "80%", objectFit: "contain" }} /> : <MarkImg brand={brand} imgStyle={{ maxHeight: 130, maxWidth: "80%", objectFit: "contain", filter }} style={{ width: 130, height: 130, filter }} />}
      </div>
      <span style={{ display: "block", fontSize: 11.5, color: "var(--b-ink-3)", marginTop: 9 }}>{label}</span>
    </div>
  );
}

function DoTile({ brand, label, ok = false, transform, filter, busy = false }: { brand: Parameters<typeof MarkImg>[0]["brand"]; label: string; ok?: boolean; transform?: string; filter?: string; busy?: boolean }) {
  return (
    <div>
      <div style={{ position: "relative", height: 150, borderRadius: "var(--radius-sm)", display: "grid", placeItems: "center", overflow: "hidden", background: busy ? "repeating-linear-gradient(45deg, #21305c 0 14px, #2c1f4e 14px 28px, #523a10 28px 42px)" : "var(--b-bg)", boxShadow: "inset 0 0 0 1px var(--b-hairline)" }}>
        <MarkImg brand={brand} imgStyle={{ height: 96, width: "auto", objectFit: "contain", transform, filter }} style={{ width: 96, height: 96, transform, filter }} />
        {!ok && <span style={{ position: "absolute", left: "8%", right: "8%", top: "50%", height: 2.5, background: "rgba(244,108,86,0.75)", transform: "rotate(-32deg)", borderRadius: 99 }} />}
      </div>
      <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 11.5, marginTop: 9, color: "var(--b-ink-2)" }}>
        <span style={{ width: 17, height: 17, borderRadius: 99, display: "grid", placeItems: "center", fontSize: 10, fontWeight: 800, background: ok ? "rgba(47,125,79,0.2)" : "rgba(179,64,46,0.22)", color: ok ? "#8be3a8" : "#f49b8a" }}>{ok ? "✓" : "x"}</span>
        {label}
      </span>
    </div>
  );
}

const smallText = { fontSize: 11.5, color: "var(--b-ink-3)", margin: "6px 0 0" } as const;

function circle(size: number) {
  return {
    position: "absolute",
    width: size,
    height: size,
    border: "1px dashed color-mix(in srgb, var(--b-accent) 22%, transparent)",
    borderRadius: 999,
  } as const;
}
