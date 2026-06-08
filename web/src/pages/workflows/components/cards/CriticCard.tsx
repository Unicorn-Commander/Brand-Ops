/**
 * On-brand critique card.
 *
 * Lets the user pick a saved asset (from api.listAssets, scoped to the active
 * brand) and judges it against the brand profile (from api.getBrand) by running
 * brand_critic. The asset's prompt is stashed onto the result so the critique's
 * tweaks can flow into the Refine card.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "../../../../components/Button";
import { Spinner } from "../../../../components/Spinner";
import { api } from "../../../../api/client";
import { WorkflowCard } from "../WorkflowCard";
import { ReadonlyField, SelectField } from "../ui";
import type { StartRun } from "./shared";
import { findWorkflow } from "../../data";
import type { AssetRecord, BrandDetail } from "../../../../lib/types";

interface CriticCardProps {
  startRun: StartRun;
  running: boolean;
  activeSlug: string | null;
  activeName: string | null;
}

const WORKFLOW = findWorkflow("brand_critic")!;

/** A stable label for one asset in the picker. */
function assetLabel(a: AssetRecord): string {
  const meta = [a.asset_type, a.variant].filter(Boolean).join(" / ");
  return `${meta || a.asset_type} · ${a.file}`;
}

/** A stable value key for an asset (index-based; the list order is stable). */
function assetKey(i: number): string {
  return String(i);
}

/** Build the asset metadata the critic reads. */
function toAssetPayload(a: AssetRecord): Record<string, unknown> {
  return {
    asset_type: a.asset_type,
    variant: a.variant,
    prompt: a.prompt ?? "",
    backend: a.backend,
    file: a.file,
  };
}

/**
 * Reduce the full brand detail to the profile the critic judges against. The
 * critic reasons over the palette, fonts, voice, and style keywords.
 */
function toBrandProfile(brand: BrandDetail): Record<string, unknown> {
  return {
    display_name: brand.display_name,
    tagline: brand.tagline,
    palette: brand.visual.colors,
    fonts: brand.visual.typography,
    style_keywords: brand.visual.style_keywords,
    voice: brand.voice,
  };
}

export function CriticCard({
  startRun,
  running,
  activeSlug,
  activeName,
}: CriticCardProps) {
  const [selected, setSelected] = useState<string>("");

  const assetsQuery = useQuery({
    queryKey: ["assets", activeSlug],
    queryFn: ({ signal }) =>
      api.listAssets({ brand: activeSlug ?? undefined }, signal),
    enabled: Boolean(activeSlug),
  });

  const brandQuery = useQuery({
    queryKey: ["brand", activeSlug],
    queryFn: ({ signal }) => api.getBrand(activeSlug as string, signal),
    enabled: Boolean(activeSlug),
  });

  const assets = useMemo(() => assetsQuery.data ?? [], [assetsQuery.data]);

  // Default to the first asset that carries a prompt (the critic is most useful
  // there), else the first asset.
  useEffect(() => {
    if (assets.length === 0) {
      setSelected("");
      return;
    }
    const stillValid = selected !== "" && Number(selected) < assets.length;
    if (stillValid) return;
    const withPrompt = assets.findIndex((a) => (a.prompt ?? "").trim() !== "");
    setSelected(assetKey(withPrompt >= 0 ? withPrompt : 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assets]);

  const chosen =
    selected !== "" && Number(selected) < assets.length
      ? assets[Number(selected)]
      : null;

  const run = () => {
    if (!chosen || !brandQuery.data) return;
    const assetPayload = toAssetPayload(chosen);
    void startRun({
      workflow: WORKFLOW,
      payload: {
        asset: assetPayload,
        brand_profile: toBrandProfile(brandQuery.data),
      },
      // Stash the prompt on the job so the result can hand it to Refine.
      basePrompt: chosen.prompt ?? "",
      inputSummary: `${activeName ?? activeSlug} · ${[chosen.asset_type, chosen.variant].filter(Boolean).join("/")}`,
    });
  };

  const loading = assetsQuery.isLoading || brandQuery.isLoading;
  const hasError = Boolean(assetsQuery.error || brandQuery.error);
  const noAssets = activeSlug && !loading && assets.length === 0;
  const promptMissing = chosen != null && (chosen.prompt ?? "").trim() === "";

  return (
    <WorkflowCard workflow={WORKFLOW}>
      <ReadonlyField
        label="Brand"
        value={activeName ?? "No brand selected"}
        muted={!activeSlug}
        hint={
          activeSlug
            ? "Judged against this brand's profile."
            : "Pick a brand in the top bar to run this."
        }
      />

      {!activeSlug ? null : loading ? (
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <Spinner size={14} /> Loading this brand's assets...
        </div>
      ) : hasError ? (
        <p className="text-xs text-red-300">
          Could not load assets for this brand.
        </p>
      ) : noAssets ? (
        <p className="text-xs text-text-muted">
          No saved assets for this brand yet. Import or generate one first.
        </p>
      ) : (
        <SelectField
          label="Asset to review"
          value={selected}
          onChange={setSelected}
          options={assets.map((a, i) => ({
            value: assetKey(i),
            label: assetLabel(a),
          }))}
          hint={
            promptMissing
              ? "This asset has no saved prompt; the critic still judges its metadata, but Refine needs a prompt."
              : "The critic reasons over the asset's prompt and metadata."
          }
        />
      )}

      <Button
        variant="primary"
        size="sm"
        onClick={run}
        loading={running}
        disabled={!chosen || !brandQuery.data || running}
      >
        {WORKFLOW.runLabel}
      </Button>
    </WorkflowCard>
  );
}
