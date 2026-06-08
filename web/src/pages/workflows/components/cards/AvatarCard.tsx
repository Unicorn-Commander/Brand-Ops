/**
 * Avatar set card.
 *
 * Runs the avatar agent as a dry run: it plans a set of avatars for the active
 * brand, composing a prompt per avatar without calling an image backend.
 */
import { useState } from "react";
import { Button } from "../../../../components/Button";
import { WorkflowCard } from "../WorkflowCard";
import { ReadonlyField, SelectField, TextField } from "../ui";
import type { StartRun } from "./shared";
import { findWorkflow } from "../../data";

interface AvatarCardProps {
  startRun: StartRun;
  running: boolean;
  activeSlug: string | null;
  activeName: string | null;
}

const COUNTS = [
  { value: "2", label: "2 avatars" },
  { value: "4", label: "4 avatars" },
  { value: "6", label: "6 avatars" },
  { value: "8", label: "8 avatars" },
];

const WORKFLOW = findWorkflow("avatar")!;

export function AvatarCard({
  startRun,
  running,
  activeSlug,
  activeName,
}: AvatarCardProps) {
  const [count, setCount] = useState("4");
  const [style, setStyle] = useState("");

  const run = () => {
    if (!activeSlug) return;
    const payload: Record<string, unknown> = {
      brand: activeSlug,
      count: Number(count),
      dry_run: true,
    };
    const styleTrimmed = style.trim();
    if (styleTrimmed) payload.style = styleTrimmed;
    void startRun({
      workflow: WORKFLOW,
      payload,
      inputSummary: `${activeName ?? activeSlug} · ${count}${styleTrimmed ? ` · ${styleTrimmed}` : ""}`,
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
        label="How many"
        value={count}
        onChange={setCount}
        options={COUNTS}
        hint="Number of avatars to plan."
      />
      <TextField
        label="Style note"
        value={style}
        onChange={setStyle}
        placeholder="Optional, e.g. portrait, flat"
        hint="A known variant locks every avatar to it; free text cycles the catalog. Optional."
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
