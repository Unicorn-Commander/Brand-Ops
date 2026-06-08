/**
 * The catalog of workflow cards.
 *
 * Each entry describes one agent run the page exposes. The `agent` field is the
 * name passed to api.runAgent; the per-card config forms live in
 * components/cards/* and own their own inputs because the agents take very
 * different payloads (a pack name vs an asset-and-brand pair vs a prior
 * critique).
 */
import {
  Package,
  ScanFace,
  ShieldCheck,
  Wand2,
  Rocket,
} from "lucide-react";
import type { WorkflowDef } from "./types";

export const WORKFLOWS: WorkflowDef[] = [
  {
    id: "asset_pack",
    agent: "asset_pack",
    label: "Asset pack",
    summary:
      "Plan a named pack of brand assets for the active brand: it composes a prompt and target path for each item.",
    steps: [
      "Load the active brand's colors, type, and references",
      "Expand the chosen pack into its asset list",
      "Compose a prompt and target size per item",
      "Return the planned pack to review (dry run)",
    ],
    icon: Package,
    runLabel: "Plan asset pack",
  },
  {
    id: "avatar",
    agent: "avatar",
    label: "Avatar set",
    summary:
      "Plan a matching set of avatars for the active brand, each with its own composed prompt.",
    steps: [
      "Read the brand's style keywords and palette",
      "Expand the count and style into avatar variants",
      "Compose a prompt per avatar",
      "Return the planned set to review (dry run)",
    ],
    icon: ScanFace,
    runLabel: "Plan avatar set",
  },
  {
    id: "brand_critic",
    agent: "brand_critic",
    label: "On-brand critique",
    summary:
      "Pick a saved asset and judge it against a brand profile: on-brand or not, a score, the reasons, and prompt tweaks.",
    steps: [
      "Pick a saved asset and the brand to judge against",
      "Send the asset's prompt and the brand profile to the critic",
      "Read back an on-brand judgment and a score",
      "Get concrete prompt tweaks to improve fit",
    ],
    icon: ShieldCheck,
    runLabel: "Run critique",
  },
  {
    id: "refine",
    agent: "refine",
    label: "Refine prompt",
    summary:
      "Take a critique's prompt tweaks and fold them back into the prompt, producing a refined prompt to regenerate from.",
    steps: [
      "Start from a critique result on this page",
      "Apply its prompt tweaks to the base prompt",
      "Smooth the merge into one natural prompt",
      "Return the refined prompt",
    ],
    icon: Wand2,
    runLabel: "Refine prompt",
  },
  {
    id: "brand_research",
    agent: "brand_research",
    label: "Brand onboarding",
    summary:
      "Research a company by name and domain and synthesize a brand profile. Needs the Brigade research hub.",
    steps: [
      "Delegate web research to the Brigade hub",
      "Synthesize a structured brand profile",
      "Draft a brand.yaml to author from",
      "Return the profile for review",
    ],
    icon: Rocket,
    runLabel: "Run research",
  },
];

/** Look up a workflow by id. Returns undefined when not found. */
export function findWorkflow(id: string): WorkflowDef | undefined {
  return WORKFLOWS.find((w) => w.id === id);
}
