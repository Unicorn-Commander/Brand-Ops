import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getBrandCompliance, listActivity, listAssets, listAssetTypes, listBrands } from "../../api/client";
import { useActiveBrand } from "../../brand-context";
import { EmptyState } from "../../components/EmptyState";
import { MarkImg } from "../../components/MarkImg";
import { Spinner } from "../../components/Spinner";
import { assetsFor, brandGrade, flaggedCount, coverageFor, relativeTime } from "../../v2/metrics";

function latest(values: Array<string | null | undefined>): string | null {
  const dates = values
    .filter((value): value is string => Boolean(value))
    .map((value) => ({ value, time: new Date(value).getTime() }))
    .filter((item) => Number.isFinite(item.time))
    .sort((a, b) => b.time - a.time);
  return dates[0]?.value ?? null;
}

export default function StandardsPage() {
  const { activeSlug } = useActiveBrand();
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const assetsQ = useQuery({ queryKey: ["assets", "all"], queryFn: ({ signal }) => listAssets({}, signal) });
  const typesQ = useQuery({ queryKey: ["asset-types"], queryFn: ({ signal }) => listAssetTypes(signal) });
  const activityQ = useQuery({ queryKey: ["activity", "standards"], queryFn: ({ signal }) => listActivity({ limit: 80 }, signal) });
  const compliancesQ = useQuery({
    queryKey: ["compliance", "all"],
    queryFn: async ({ signal }) => {
      const brands = await listBrands(signal);
      return Promise.all(
        brands.map(async (brand) => ({
          slug: brand.slug,
          brand,
          compliance: await getBrandCompliance(brand.slug, signal),
        })),
      );
    },
  });

  if (brandsQ.isLoading || assetsQ.isLoading || typesQ.isLoading || activityQ.isLoading || compliancesQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandsQ.data || !assetsQ.data || !activityQ.data || !compliancesQ.data) {
    return <EmptyState title="Standards unavailable" description="The compliance board could not load." />;
  }

  const assets = assetsQ.data;
  const types = typesQ.data ?? [];
  const activity = activityQ.data;
  const compliances = compliancesQ.data;
  const brandNameBySlug = new Map(brandsQ.data.map((brand) => [brand.slug, brand.display_name]));
  const selected = compliances.find((row) => row.slug === activeSlug) ?? compliances[0];
  const selectedAssets = assetsFor(assets, selected.slug);
  const selectedCoverage = coverageFor(selectedAssets, types);
  const selectedGrade = brandGrade(selectedAssets, types, selected.compliance);
  const selectedDimensions = selectedGrade.dimensions;
  const board = compliances.map((row) => {
    const rowAssets = assetsFor(assets, row.slug);
    const coverage = coverageFor(rowAssets, types);
    const flagged = flaggedCount(rowAssets);
    const grade = brandGrade(rowAssets, types, row.compliance);
    const dimensions = grade.dimensions;
    const newestAssetStamp = latest(rowAssets.map((asset) => asset.compliance ? asset.created_at : null));
    const newestActivityStamp = latest(activity.filter((event) => event.brand === row.slug).map((event) => event.ts));
    const lastAudit = newestAssetStamp ?? newestActivityStamp;
    return { ...row, coverage, flagged, dimensions, grade, lastAudit };
  });

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">Standards</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            Graded like it ships.
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            Weighted from live data only. Coverage comes from the catalog, compliance from the gate, and the report card is honest about what is still missing.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn btn-ghost" to="/library">
            Audit history
          </Link>
          <Link className="btn btn-accent" to={`/b/${selected.slug}/studio`}>
            Fix the biggest lever
          </Link>
        </div>
      </div>

      <div
        style={{
          marginTop: 28,
          border: "1px solid var(--b-hairline)",
          borderRadius: "var(--radius)",
          overflow: "hidden",
          background: "rgba(255,255,255,0.02)",
        }}
      >
        <div className="board-head" style={{ display: "grid", gap: 18, padding: "12px 22px", color: "var(--b-ink-3)", fontSize: 10, letterSpacing: "0.13em", textTransform: "uppercase" }}>
          <span>Brand</span>
          <span>Grade</span>
          <span>Dimensions</span>
          <span>Open issues</span>
          <span>Last audit</span>
        </div>
        {board.map((row) => (
          <div
            key={row.slug}
            className="board-row"
            style={{
              display: "grid",
              gap: 18,
              alignItems: "center",
              padding: "14px 22px",
              borderTop: "1px solid rgba(255,255,255,0.05)",
              background: row.slug === activeSlug ? "var(--c-bg-2)" : "transparent",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
              <span className="mat" style={{ width: 40, height: 40, flex: "none", background: "var(--mat-dark)" }}>
                <MarkImg brand={row.brand} style={{ width: 30, height: 30, borderRadius: 9, fontSize: 16 }} imgStyle={{ maxWidth: 30, maxHeight: 30 }} />
              </span>
              <div style={{ minWidth: 0 }}>
                <b style={{ display: "block" }}>{row.brand.display_name}</b>
                <span style={{ color: "var(--b-ink-3)", fontSize: 10.5 }}>{row.brand.parent ? `Part of ${brandNameBySlug.get(row.brand.parent) ?? row.brand.parent}` : "masterbrand"}</span>
              </div>
            </div>
            <span className={`grade-letter ${row.grade.className}`}>{row.grade.letter}</span>
            <div style={{ display: "flex", gap: 8 }}>
              {row.dimensions.map((dimension) => (
                <div key={dimension.key} style={{ flex: 1 }} title={dimension.note}>
                  <div style={{ fontSize: 9, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--b-ink-3)", whiteSpace: "nowrap" }}>
                    {dimension.label}{dimension.measured ? "" : " *"}
                  </div>
                  <div style={{ height: 4, borderRadius: 99, background: "var(--c-bg-2)", overflow: "hidden" }}>
                    <i
                      style={{
                        display: "block",
                        height: "100%",
                        width: `${dimension.score}%`,
                        background: dimension.score >= 80 ? "var(--c-go)" : dimension.score >= 60 ? "var(--c-warn)" : "var(--c-stop)",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
            <span style={{ color: row.flagged ? "var(--c-warn)" : "var(--c-go)", fontWeight: 600 }}>{row.flagged}</span>
            <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 11, color: "var(--b-ink-3)" }}>{row.lastAudit ? relativeTime(row.lastAudit) : "not audited"}</span>
          </div>
        ))}
      </div>

      <div className="report-grid" style={{ display: "grid", gap: 26, marginTop: 30 }}>
        <aside
          style={{
            border: "1px solid var(--b-hairline)",
            borderRadius: "var(--radius)",
            background: "rgba(255,255,255,0.03)",
            padding: 26,
          }}
        >
          <span className="microlabel">Report card</span>
          <div style={{ marginTop: 14, display: "grid", placeItems: "center" }}>
            <div style={{ width: 150, height: 150, borderRadius: 999, border: "9px solid var(--c-go)", display: "grid", placeItems: "center" }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 38, fontFamily: "var(--b-font-display)", fontWeight: 800, color: "var(--c-go)" }}>{selectedGrade.letter}</div>
                <div style={{ fontSize: 11, color: "var(--b-ink-3)" }}>{selectedGrade.score} / 100</div>
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginTop: 10 }}>
            <span className="chip">{selected.brand.display_name}</span>
          </div>
          <p style={{ color: "var(--b-ink-3)", fontSize: 12.5, marginTop: 12 }}>
            Biggest lever: coverage. {selectedCoverage.pct}% of the required set is accounted for.
          </p>
          <Link className="btn btn-ink" to={`/b/${selected.slug}/studio`} style={{ marginTop: 14 }}>
            Fix the biggest lever
          </Link>
        </aside>

        <section style={{ display: "grid", gap: 18 }}>
          <div className="voice-card">
            <span className="microlabel">Dimensions</span>
            <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
              {selectedDimensions.map((dimension) => (
                <div key={dimension.key} className="dim-row" style={{ display: "grid", gap: 14, alignItems: "center" }}>
                  <div>
                    <b style={{ display: "block", fontSize: 13 }}>{dimension.label}</b>
                    <span style={{ color: "var(--b-ink-3)", fontSize: 10.5 }}>{dimension.note}</span>
                  </div>
                  <div style={{ height: 7, borderRadius: 99, background: "var(--c-bg-2)", overflow: "hidden" }}>
                    <i
                      style={{
                        display: "block",
                        height: "100%",
                        width: `${dimension.score}%`,
                        borderRadius: 99,
                        background: dimension.score >= 80 ? "var(--c-go)" : dimension.score >= 60 ? "var(--c-warn)" : "var(--c-stop)",
                      }}
                    />
                  </div>
                  <span style={{ fontFamily: "var(--c-font-mono)", fontSize: 12, color: "var(--b-ink-2)", textAlign: "right" }}>{dimension.score}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="voice-card">
            <span className="microlabel">Issues</span>
            <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
              {board.filter((row) => row.flagged > 0).slice(0, 3).map((row) => (
                <div key={row.slug} style={{ border: "1px solid var(--b-hairline)", borderRadius: "var(--radius-sm)", padding: 16 }}>
                  <b>{row.brand.display_name}</b>
                  <p style={{ color: "var(--b-ink-3)", margin: "4px 0 0" }}>{row.flagged} open review item(s) and {Math.max(0, 6 - row.coverage.pct)} missing coverage points.</p>
                </div>
              ))}
            </div>
          </div>

          <div className="voice-card">
            <span className="microlabel">Methodology</span>
            <p style={{ color: "var(--b-ink-2)" }}>
              Weights remain anchored to live data. This board recomputes from assets and compliance, without inventing any new backend values.
            </p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {["color 25", "type 15", "mark 20", "coverage 25", "voice 15"].map((label) => (
                <span key={label} className="chip">{label}</span>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
