/**
 * Compliance section for the brand sheet. Fetches the brand-wide rollup
 * (GET /brands/{slug}/compliance), which runs the deterministic brand check
 * over every catalog asset, then shows a tinted pass/warn/fail summary and a
 * per-asset list. Each asset carries a status pill, its score, and an
 * expandable list of human-readable violations. This is the visible gate:
 * which existing assets are on-brand vs off-brand.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ChevronDown } from "lucide-react";
import { getBrandCompliance } from "../../../api/client";
import { GlassPanel } from "../../../components/GlassPanel";
import { Spinner } from "../../../components/Spinner";
import { EmptyState } from "../../../components/EmptyState";
import type {
  ComplianceAsset,
  ComplianceStatus,
  ComplianceSummary,
} from "../../../lib/types";

/** Tailwind classes per status, reused by the pills and the summary tallies. */
const STATUS_STYLE: Record<ComplianceStatus, { pill: string; text: string }> = {
  pass: {
    pill: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    text: "text-emerald-300",
  },
  warn: {
    pill: "border-amber-400/30 bg-amber-400/10 text-amber-300",
    text: "text-amber-300",
  },
  fail: {
    pill: "border-red-500/30 bg-red-500/10 text-red-400",
    text: "text-red-400",
  },
  skipped: {
    pill: "border-white/10 bg-white/[0.03] text-text-faint",
    text: "text-text-faint",
  },
};

const STATUS_LABEL: Record<ComplianceStatus, string> = {
  pass: "Pass",
  warn: "Warn",
  fail: "Fail",
  skipped: "Skipped",
};

/** One tinted count in the summary header. */
function Tally({
  status,
  count,
}: {
  status: ComplianceStatus;
  count: number;
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span
        className={`text-lg font-semibold tabular-nums ${STATUS_STYLE[status].text}`}
      >
        {count}
      </span>
      <span className="text-xs text-text-faint">{STATUS_LABEL[status]}</span>
    </div>
  );
}

/** The pass/warn/fail tally plus the brand's average score. */
function SummaryHeader({ summary }: { summary: ComplianceSummary }) {
  // Tint the overall score by where it lands: high is on-brand, low is off.
  const scoreTone =
    summary.fail > 0 || summary.score < 60
      ? STATUS_STYLE.fail.text
      : summary.warn > 0 || summary.score < 85
        ? STATUS_STYLE.warn.text
        : STATUS_STYLE.pass.text;

  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-4 rounded-xl border border-white/8 bg-white/[0.02] px-4 py-3">
      <div className="flex flex-wrap items-center gap-5">
        <Tally status="pass" count={summary.pass} />
        <Tally status="warn" count={summary.warn} />
        <Tally status="fail" count={summary.fail} />
        {summary.skipped > 0 && (
          <Tally status="skipped" count={summary.skipped} />
        )}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-xs text-text-faint">Overall</span>
        <span className={`text-2xl font-semibold tabular-nums ${scoreTone}`}>
          {Math.round(summary.score)}
        </span>
        <span className="text-xs text-text-faint">/ 100</span>
      </div>
    </div>
  );
}

/** One asset row: status pill, file name, score, and expandable violations. */
function AssetRow({ asset }: { asset: ComplianceAsset }) {
  const [open, setOpen] = useState(false);
  const style = STATUS_STYLE[asset.status];
  const hasViolations = asset.violations.length > 0;

  return (
    <li className="rounded-xl border border-white/8 bg-white/[0.02]">
      <button
        type="button"
        onClick={() => hasViolations && setOpen((v) => !v)}
        className={`flex w-full items-center gap-3 px-3 py-2.5 text-left ${
          hasViolations ? "cursor-pointer hover:bg-white/[0.03]" : "cursor-default"
        }`}
        // Without violations there is nothing to expand, so the row is inert.
        title={hasViolations ? `${asset.violations.length} violations` : undefined}
      >
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium ${style.pill}`}
        >
          {STATUS_LABEL[asset.status]}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-text">{asset.file}</span>
          <span className="block text-xs text-text-faint">
            {asset.asset_type} · {asset.kind}
          </span>
        </span>
        <span className={`shrink-0 text-sm font-medium tabular-nums ${style.text}`}>
          {Math.round(asset.score)}
        </span>
        {hasViolations && (
          <ChevronDown
            size={15}
            className={`shrink-0 text-text-faint transition-transform ${
              open ? "rotate-180" : ""
            }`}
          />
        )}
      </button>
      {open && hasViolations && (
        <ul className="space-y-1 border-t border-white/8 px-3 py-2">
          {asset.violations.map((v, i) => (
            <li key={i} className="flex gap-2 text-xs text-text-muted">
              <span className="text-text-faint">·</span>
              <span>{v}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ComplianceSection({ slug }: { slug: string }) {
  const complianceQ = useQuery({
    queryKey: ["compliance", slug],
    queryFn: ({ signal }) => getBrandCompliance(slug, signal),
    enabled: Boolean(slug),
  });

  return (
    <GlassPanel
      ring
      title={
        <span className="flex items-center gap-2">
          <ShieldCheck size={15} className="text-accent" />
          Compliance
        </span>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-text-muted">
        Every catalog asset checked against the brand rules. The gate shows which
        assets are on-brand and which drift off it.
      </p>

      {complianceQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : complianceQ.isError ? (
        <EmptyState
          title="Could not run compliance"
          description={
            complianceQ.error instanceof Error
              ? complianceQ.error.message
              : "The compliance check failed to load."
          }
        />
      ) : !complianceQ.data || complianceQ.data.assets.length === 0 ? (
        <EmptyState
          title="No assets to check"
          description="Generate or upload assets for this brand to see their compliance here."
        />
      ) : (
        <>
          <SummaryHeader summary={complianceQ.data.summary} />
          <ul className="space-y-2">
            {complianceQ.data.assets.map((a, i) => (
              <AssetRow key={`${a.file}-${i}`} asset={a} />
            ))}
          </ul>
        </>
      )}
    </GlassPanel>
  );
}
