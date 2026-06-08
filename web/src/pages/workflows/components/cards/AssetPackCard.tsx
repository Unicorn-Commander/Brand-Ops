/**
 * Asset pack card.
 *
 * Runs asset_pack as a dry run: it plans a named pack for the active brand and
 * returns one composed prompt and target path per item, without calling an
 * image backend.
 */
import { useState } from "react";
import { Button } from "../../../../components/Button";
import { WorkflowCard } from "../WorkflowCard";
import { ReadonlyField, SelectField } from "../ui";
import type { StartRun } from "./shared";
import { findWorkflow } from "../../data";

interface AssetPackCardProps {
  startRun: StartRun;
  running: boolean;
  activeSlug: string | null;
  activeName: string | null;
}

/** The named packs the asset_pack agent knows (from PACKS in asset_pack.py). */
const PACKS = [
  { value: "core", label: "Core (logo, app icon, OG)" },
  { value: "social", label: "Social (cards + OG)" },
  { value: "logo", label: "Logo set (flat, mono, color, icon)" },
  { value: "podcast", label: "Podcast (cover + social)" },
  { value: "launch", label: "Launch (full set)" },
];

const WORKFLOW = findWorkflow("asset_pack")!;

export function AssetPackCard({
  startRun,
  running,
  activeSlug,
  activeName,
}: AssetPackCardProps) {
  const [pack, setPack] = useState("core");

  const run = () => {
    if (!activeSlug) return;
    void startRun({
      workflow: WORKFLOW,
      payload: { brand: activeSlug, pack, dry_run: true },
      inputSummary: `${activeName ?? activeSlug} · ${pack}`,
    });
  };

  return (
    <WorkflowCard workflow={WORKFLOW}>
      <ReadonlyField
        label="Brand"
        value={activeName ?? "No brand selected"}
        muted={!activeSlug}
        hint={
          activeSlug
            ? "Uses the brand selected in the top bar."
            : "Pick a brand in the top bar to run this."
        }
      />
      <SelectField
        label="Pack"
        value={pack}
        onChange={setPack}
        options={PACKS}
        hint="Plans every asset in the pack. This is a dry run, so nothing is generated."
      />
      <Button
        variant="primary"
        size="sm"
        onClick={run}
        loading={running}
        disabled={!activeSlug || running}
      >
        {WORKFLOW.runLabel}
      </Button>
    </WorkflowCard>
  );
}
