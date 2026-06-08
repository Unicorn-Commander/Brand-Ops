/**
 * Brand onboarding card.
 *
 * Runs brand_research over a company name and domain. The agent delegates web
 * research to the Brigade hub and synthesizes a brand profile with a strong
 * model. Without Brigade (the common case here) it returns a graceful
 * research_only / no-context status, which the result panel labels as needing
 * Brigade.
 */
import { useState } from "react";
import { Button } from "../../../../components/Button";
import { WorkflowCard } from "../WorkflowCard";
import { Notice, TextField } from "../ui";
import type { StartRun } from "./shared";
import { findWorkflow } from "../../data";

interface ResearchCardProps {
  startRun: StartRun;
  running: boolean;
}

const WORKFLOW = findWorkflow("brand_research")!;

export function ResearchCard({ startRun, running }: ResearchCardProps) {
  const [company, setCompany] = useState("");
  const [domain, setDomain] = useState("");

  const run = () => {
    const companyTrimmed = company.trim();
    if (!companyTrimmed) return;
    const payload: Record<string, unknown> = { company: companyTrimmed };
    const domainTrimmed = domain.trim();
    if (domainTrimmed) payload.domain = domainTrimmed;
    void startRun({
      workflow: WORKFLOW,
      payload,
      inputSummary: domainTrimmed
        ? `${companyTrimmed} · ${domainTrimmed}`
        : companyTrimmed,
    });
  };

  return (
    <WorkflowCard workflow={WORKFLOW}>
      <Notice tone="info" title="Needs the Brigade hub">
        Research delegates to the Brigade hub. If it is not connected here, the
        run returns a clear status instead of a profile.
      </Notice>
      <TextField
        label="Company"
        value={company}
        onChange={setCompany}
        placeholder="e.g. Northwind Labs"
        hint="The company or brand to research."
      />
      <TextField
        label="Domain"
        value={domain}
        onChange={setDomain}
        type="url"
        placeholder="Optional, e.g. northwind.example"
        hint="The primary web domain, if known. Optional."
      />
      <Button
        variant="primary"
        size="sm"
        onClick={run}
        loading={running}
        disabled={company.trim() === "" || running}
      >
        {WORKFLOW.runLabel}
      </Button>
    </WorkflowCard>
  );
}
