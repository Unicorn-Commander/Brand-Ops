import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Download, Layers, Plus, Sparkles, Volume2 } from "lucide-react";

import {
  agentManifestUrl,
  createBrandAgent,
  generateAgentVariants,
  generateBrandAgentAvatar,
  getAgentTeam,
  listVoices,
  previewVoice,
  setBrandAgentVoice,
} from "../../../api/client";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { Button } from "../../../components/Button";
import { EmptyState } from "../../../components/EmptyState";
import { GlassPanel } from "../../../components/GlassPanel";
import { Spinner } from "../../../components/Spinner";
import type {
  AgentIdentity,
  AgentTeamMember,
  GovernanceStatus,
  VoiceCatalog,
} from "../../../lib/types";

const INPUT =
  "w-full rounded-lg border border-white/10 bg-chrome-900/60 px-3 py-2 text-sm " +
  "text-text placeholder:text-text-faint accent-ring focus:outline-none";

const SELECT =
  "rounded-lg border border-white/10 bg-chrome-900/60 px-2.5 py-1.5 text-sm " +
  "text-text accent-ring focus:outline-none";

/**
 * One agent's spoken-voice control: the bound voice (or none), a picker that
 * assigns from the catalog on change, and a preview button that plays a short
 * sample when an orator is wired. The catalog is loaded once by the panel and
 * passed down. Brand-Ops owns the voice; Stable/orator speak it.
 */
function VoiceControl({
  slug,
  agent,
  catalog,
  onChanged,
}: {
  slug: string;
  agent: AgentIdentity;
  catalog: VoiceCatalog | undefined;
  onChanged: () => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const current = agent.voice?.voice_id ?? "";

  const setVoice = useMutation({
    mutationFn: (voiceId: string) =>
      setBrandAgentVoice(slug, agent.slug, { voice_id: voiceId || null }),
    onSuccess: onChanged,
  });

  async function preview() {
    if (!current) return;
    setPreviewing(true);
    try {
      const blob = await previewVoice({ voice_id: current });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      await audio.play();
    } catch {
      // Best-effort: preview needs a reachable orator; a failure is non-fatal and
      // leaves the binding untouched.
    } finally {
      setPreviewing(false);
    }
  }

  const custom = (catalog?.voices ?? []).filter((v) => v.kind === "custom");
  const builtin = (catalog?.voices ?? []).filter((v) => v.kind !== "custom");

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-text-faint">
        <Volume2 size={12} className="text-accent" />
        Voice
      </span>
      <select
        className={SELECT}
        value={current}
        disabled={setVoice.isPending || !catalog}
        onChange={(e) => setVoice.mutate(e.target.value)}
        aria-label={`Spoken voice for ${agent.name}`}
      >
        <option value="">No voice</option>
        {custom.length > 0 ? (
          <optgroup label="Premium">
            {custom.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
                {v.descriptor ? ` - ${v.descriptor}` : ""}
              </option>
            ))}
          </optgroup>
        ) : null}
        {builtin.length > 0 ? (
          <optgroup label="Standard">
            {builtin.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
                {v.descriptor ? ` - ${v.descriptor}` : ""}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
      {current && catalog?.preview_available ? (
        <button
          type="button"
          onClick={preview}
          disabled={previewing}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-text-muted hover:text-text disabled:opacity-60"
        >
          <Volume2 size={14} />
          {previewing ? "Playing..." : "Preview"}
        </button>
      ) : null}
      {setVoice.isError ? (
        <span className="text-[11px] text-rose-400">Could not save</span>
      ) : null}
    </div>
  );
}

/** Visual treatment for an agent's governance badge, keyed by status. */
const BADGE: Record<GovernanceStatus, { label: string; className: string }> = {
  on_brand: {
    label: "On-brand",
    className: "border-accent/30 bg-accent/10 text-accent",
  },
  needs_review: {
    label: "Needs review",
    className: "border-amber-400/30 bg-amber-400/10 text-amber-300",
  },
  unrated: {
    label: "Unrated",
    className: "border-white/10 bg-white/5 text-text-faint",
  },
};

/** A small pill badging one agent's governance verdict, no re-derivation. */
function GovernanceBadge({ status }: { status: GovernanceStatus }) {
  const badge = BADGE[status];
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${badge.className}`}
    >
      {badge.label}
    </span>
  );
}

/**
 * One compact row in the orchestration tier: the Director (Iris) or the
 * Steward. The Director renders the orb fallback (no src); the Steward renders
 * the brand mark when set, else the same orb.
 */
function OrchestrationRow({ member }: { member: AgentTeamMember }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-chrome-900/40 px-3 py-2.5">
      <AgentAvatar name={member.name} src={member.avatar_url} size={40} />
      <div className="min-w-0">
        <div className="truncate font-display text-sm font-semibold text-text">
          {member.name}
        </div>
        <div className="truncate text-[11px] font-medium uppercase tracking-wider text-accent">
          {member.role}
        </div>
      </div>
    </div>
  );
}

/**
 * The brand's agent TEAM: the orchestration tier (the Director Iris + the
 * per-brand Steward) over the brand's created agents, each an on-brand identity
 * (avatar + persona + manifest) carrying a governance badge. Create an agent,
 * generate its on-brand avatar and variant set through the gateway, and export a
 * manifest the suite/Brigade can consume. This is how a company's agents come
 * out on-brand by construction.
 */
export function AgentsPanel({ slug, brandName }: { slug: string; brandName: string }) {
  const qc = useQueryClient();
  const teamQ = useQuery({
    queryKey: ["agent-team", slug],
    queryFn: ({ signal }) => getAgentTeam(slug, signal),
  });
  // The voice catalog is brand-agnostic; load it once and share across cards.
  const voicesQ = useQuery({
    queryKey: ["voices"],
    queryFn: ({ signal }) => listVoices(signal),
    staleTime: 5 * 60 * 1000,
  });

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [bio, setBio] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refetchTeam = () =>
    void qc.invalidateQueries({ queryKey: ["agent-team", slug] });

  const create = useMutation({
    mutationFn: () =>
      createBrandAgent(slug, { name: name.trim(), role: role.trim(), bio: bio.trim() }),
    onSuccess: () => {
      setName("");
      setRole("");
      setBio("");
      setOpen(false);
      setError(null);
      refetchTeam();
    },
    onError: (e: unknown) =>
      setError(e instanceof Error ? e.message : "Could not create the agent."),
  });

  const genAvatar = useMutation({
    mutationFn: (agentSlug: string) => generateBrandAgentAvatar(slug, agentSlug),
    onSuccess: refetchTeam,
  });

  const genVariants = useMutation({
    mutationFn: (agentSlug: string) => generateAgentVariants(slug, agentSlug),
    onSuccess: refetchTeam,
  });

  const team = teamQ.data;
  const agents = team?.agents ?? [];
  const version = teamQ.dataUpdatedAt; // cache-buster so a regenerated avatar refreshes

  return (
    <GlassPanel
      ring
      title={
        <span className="flex items-center gap-2">
          <Bot size={15} className="text-accent" />
          Agent team
        </span>
      }
      actions={
        <Button variant="secondary" onClick={() => setOpen((v) => !v)}>
          <Plus size={14} />
          New agent
        </Button>
      }
    >
      <p className="-mt-1 mb-4 max-w-2xl text-sm leading-relaxed text-text-muted">
        On-brand identities for the agents that work {brandName}. Each inherits the
        brand voice and palette, so an agent reads as part of the brand wherever it
        appears. Export a manifest for the suite or Brigade to render it.
      </p>

      {open ? (
        <form
          className="mb-5 grid gap-3 rounded-xl border border-white/10 bg-chrome-900/40 p-4 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && role.trim()) create.mutate();
          }}
        >
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-faint">Name</span>
            <input
              className={INPUT}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Iris"
              autoFocus
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-text-faint">Role</span>
            <input
              className={INPUT}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              placeholder="brand director"
            />
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="text-xs text-text-faint">Bio (optional)</span>
            <input
              className={INPUT}
              value={bio}
              onChange={(e) => setBio(e.target.value)}
              placeholder="What this agent does for the brand."
            />
          </label>
          <div className="flex items-center gap-2 sm:col-span-2">
            <Button
              variant="primary"
              type="submit"
              loading={create.isPending}
              disabled={!name.trim() || !role.trim()}
            >
              Create agent
            </Button>
            <Button variant="ghost" type="button" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            {error ? <span className="text-sm text-rose-400">{error}</span> : null}
          </div>
        </form>
      ) : null}

      {teamQ.isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : !team ? (
        <EmptyState
          icon={<Bot size={20} />}
          title="No agent team yet"
          description={`Create the first agent for ${brandName} and generate its on-brand avatar.`}
        />
      ) : (
        <>
          <p className="mb-4 text-sm text-text-muted">
            {team.governance.total === 0
              ? "No agents yet."
              : `${team.governance.total} agent${
                  team.governance.total === 1 ? "" : "s"
                }, ${team.governance.on_brand} on-brand`}
            {team.governance.needs_review > 0 ? (
              <span className="text-amber-300">
                {" "}
                ({team.governance.needs_review} need
                {team.governance.needs_review === 1 ? "s" : ""} review)
              </span>
            ) : null}
          </p>

          <div className="mb-5">
            <h4 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-text-faint">
              Orchestration
            </h4>
            <div className="grid gap-2 sm:grid-cols-2">
              <OrchestrationRow member={team.director} />
              <OrchestrationRow member={team.steward} />
            </div>
          </div>

          {agents.length === 0 ? (
            <EmptyState
              icon={<Bot size={20} />}
              title="No agents yet"
              description={`Create the first agent for ${brandName} and generate its on-brand avatar.`}
            />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {agents.map((a: AgentIdentity) => {
                const generating = genAvatar.isPending && genAvatar.variables === a.slug;
                const generatingVariants =
                  genVariants.isPending && genVariants.variables === a.slug;
                const src = a.avatar_url ? `${a.avatar_url}?v=${version}` : null;
                const variantStates = Object.entries(a.variants ?? {});
                const tones = a.voice_tone ?? [];
                return (
                  <div
                    key={a.slug}
                    className="flex gap-4 rounded-xl border border-white/10 bg-chrome-900/40 p-4"
                  >
                    <AgentAvatar name={a.name} src={src} size={56} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <h3 className="truncate font-display text-base font-semibold text-text">
                          {a.name}
                        </h3>
                        <span className="text-xs font-medium uppercase tracking-wider text-accent">
                          {a.role}
                        </span>
                        <GovernanceBadge status={a.governance_status} />
                      </div>
                      {a.bio ? (
                        <p className="mt-1 text-sm leading-relaxed text-text-muted">{a.bio}</p>
                      ) : null}
                      {tones.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {tones.slice(0, 5).map((t) => (
                            <span
                              key={t}
                              className="rounded-full border border-white/10 bg-white/5 px-2 py-0.5 text-[11px] text-text-muted"
                            >
                              {t}
                            </span>
                          ))}
                        </div>
                      ) : null}
                      <VoiceControl
                        slug={slug}
                        agent={a}
                        catalog={voicesQ.data}
                        onChanged={refetchTeam}
                      />
                      {variantStates.length > 0 ? (
                        <div className="mt-3 flex flex-wrap gap-3">
                          {variantStates.map(([state, url]) => (
                            <div key={state} className="flex flex-col items-center gap-1">
                              <AgentAvatar
                                name={a.name}
                                src={`${url}?v=${version}`}
                                size={40}
                              />
                              <span className="text-[11px] capitalize text-text-faint">
                                {state}
                              </span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                      <div className="mt-3 flex flex-wrap items-center gap-2">
                        <Button
                          variant="secondary"
                          onClick={() => genAvatar.mutate(a.slug)}
                          loading={generating}
                        >
                          <Sparkles size={14} />
                          {a.avatar_url ? "Regenerate avatar" : "Generate avatar"}
                        </Button>
                        <Button
                          variant="secondary"
                          onClick={() => genVariants.mutate(a.slug)}
                          loading={generatingVariants}
                        >
                          <Layers size={14} />
                          Generate variant set
                        </Button>
                        <a
                          href={agentManifestUrl(slug, a.slug)}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm text-text-muted hover:text-text"
                        >
                          <Download size={14} />
                          Manifest
                        </a>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </GlassPanel>
  );
}
