/**
 * Refine prompt card.
 *
 * Takes a critique's prompt tweaks and folds them back into a base prompt by
 * running the refine agent. The primary flow is the "Send tweaks to Refine"
 * button on a critique result, which seeds this card with the asset's prompt
 * and the critique. The base prompt stays editable; a user can also paste their
 * own prompt and supply tweaks by hand.
 */
import { useEffect, useState } from "react";
import { Button } from "../../../../components/Button";
import { WorkflowCard } from "../WorkflowCard";
import {
  Notice,
  BulletList,
  FIELD_LABEL,
  FIELD_HINT,
  INPUT_BASE,
} from "../ui";
import type { StartRun } from "./shared";
import { findWorkflow } from "../../data";
import type { CriticResult } from "../../types";

/** What a critique hands to this card via the page. */
export interface RefineSeed {
  basePrompt: string;
  critique: CriticResult;
  /** Bumped each time a new seed is pushed, so the card re-syncs. */
  nonce: number;
}

interface RefineCardProps {
  startRun: StartRun;
  running: boolean;
  /** Set when a critique sends its tweaks here; null otherwise. */
  seed: RefineSeed | null;
}

const WORKFLOW = findWorkflow("refine")!;

export function RefineCard({ startRun, running, seed }: RefineCardProps) {
  const [prompt, setPrompt] = useState("");
  const [tweaksText, setTweaksText] = useState("");

  // Re-sync the editable fields whenever a new seed arrives.
  useEffect(() => {
    if (!seed) return;
    setPrompt(seed.basePrompt);
    const tweaks = seed.critique.critique?.prompt_tweaks ?? [];
    setTweaksText(tweaks.join("\n"));
  }, [seed?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const tweaks = tweaksText
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);

  const canRun = prompt.trim() !== "" && tweaks.length > 0 && !running;

  const run = () => {
    if (prompt.trim() === "" || tweaks.length === 0) return;
    void startRun({
      workflow: WORKFLOW,
      // The refine agent's extract_tweaks accepts a bare critique dict whose
      // prompt_tweaks drive the merge.
      payload: {
        asset: { prompt: prompt.trim() },
        critique: { prompt_tweaks: tweaks },
        smooth: true,
      },
      inputSummary: `${tweaks.length} tweak${tweaks.length === 1 ? "" : "s"}`,
    });
  };

  return (
    <WorkflowCard workflow={WORKFLOW}>
      {!seed && (
        <Notice tone="info" title="Start from a critique">
          Run On-brand critique first, then use "Send tweaks to Refine" on its
          result. You can also paste a prompt and tweaks here directly.
        </Notice>
      )}

      <div>
        <label className={FIELD_LABEL}>
          Base prompt
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={3}
            placeholder="The prompt to refine"
            className={`${INPUT_BASE} resize-y`}
          />
        </label>
        <p className={FIELD_HINT}>The prompt the tweaks are folded into.</p>
      </div>

      <div>
        <label className={FIELD_LABEL}>
          Prompt tweaks
          <textarea
            value={tweaksText}
            onChange={(e) => setTweaksText(e.target.value)}
            rows={3}
            placeholder="One tweak per line"
            className={`${INPUT_BASE} resize-y`}
          />
        </label>
        <p className={FIELD_HINT}>One tweak per line. Seeded from a critique.</p>
      </div>

      {tweaks.length > 0 && (
        <BulletList label="Will apply" items={tweaks} accent />
      )}

      <Button
        variant="primary"
        size="sm"
        onClick={run}
        loading={running}
        disabled={!canRun}
      >
        {WORKFLOW.runLabel}
      </Button>
    </WorkflowCard>
  );
}
