import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getBrand, listAssets } from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";

function assetCountByType(assets: { asset_type: string }[], key: string): number {
  return assets.filter((asset) => asset.asset_type === key).length;
}

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
  const markUrl = brand.mark_url ?? undefined;

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 22, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">The mark</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            {brand.display_name}
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            Canonical mark, usage notes, lineage, and approved uses derived from the brand record and catalog.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn btn-ghost" to={`/b/${slug}/studio`}>
            Ask why she looks this way
          </Link>
          <Link className="btn btn-ghost" to={`/b/${slug}/book`}>
            Brand book
          </Link>
          <Link className="btn btn-accent" to={`/b/${slug}/studio?asset=logo`}>
            Commission variant
          </Link>
        </div>
      </div>

      <div
        style={{
          marginTop: 26,
          border: "1px solid var(--b-hairline)",
          borderRadius: "var(--radius)",
          background: "rgba(255,255,255,0.02)",
          padding: 36,
        }}
      >
        <div
          style={{
            minHeight: 420,
            display: "grid",
            placeItems: "center",
            borderRadius: "var(--radius)",
            border: "1px dashed color-mix(in srgb, var(--b-accent) 45%, transparent)",
            background:
              "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px), rgba(255,255,255,0.01)",
            backgroundSize: "44px 44px",
          }}
        >
          {markUrl ? <img src={markUrl} alt={`${brand.display_name} mark`} style={{ maxHeight: 320, maxWidth: "70%", objectFit: "contain" }} /> : null}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18, marginTop: 22 }}>
        <div className="voice-card">
          <span className="microlabel">Clear space</span>
          <p style={{ color: "var(--b-ink-2)" }}>Measured from the published asset only. Keep the field open on every side.</p>
        </div>
        <div className="voice-card">
          <span className="microlabel">Minimum size</span>
          <p style={{ color: "var(--b-ink-2)" }}>{assetCountByType(assets, "logo")} logo records available for rendering checks.</p>
        </div>
        <div className="voice-card">
          <span className="microlabel">Lineage</span>
          <p style={{ color: "var(--b-ink-2)" }}>
            {brand.parent ? `Refers back to ${brand.parent}.` : "This is the root brand mark."}
          </p>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, marginTop: 22 }}>
        <div className="voice-card">
          <span className="microlabel">Approved uses</span>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 12, marginTop: 12 }}>
            <div className="mat" style={{ minHeight: 150 }}>{markUrl ? <img src={markUrl} alt="" /> : null}</div>
            <div className="mat dark" style={{ minHeight: 150 }}>{markUrl ? <img src={markUrl} alt="" /> : null}</div>
          </div>
        </div>
        <div className="voice-card">
          <span className="microlabel">Do / don't</span>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 12, marginTop: 12 }}>
            <div className="mat" style={{ minHeight: 150 }}>{markUrl ? <img src={markUrl} alt="" /> : null}</div>
            <div className="mat dark" style={{ minHeight: 150 }}>{markUrl ? <img src={markUrl} alt="" /> : null}</div>
          </div>
          <p style={{ color: "var(--b-ink-3)", marginTop: 12 }}>
            Never stretch, recolor, or crop the mark. Busy backgrounds stay out of the way.
          </p>
        </div>
      </div>
    </div>
  );
}
