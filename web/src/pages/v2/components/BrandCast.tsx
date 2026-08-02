import { useState, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, FileJson, Layers, Sparkles, Volume2 } from "lucide-react";

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
import { Spinner } from "../../../components/Spinner";
import { downloadFile, fileNameFor } from "../../../lib/download";
import type { AgentIdentity, GovernanceStatus } from "../../../lib/types";

// ---------------------------------------------------------------------------
// Local inline styles, mirroring CrewPage's daylight/paper idiom. These match
// the field treatments CrewPage defines (selectStyle/saveButtonStyle/inputStyle/
// textareaStyle) so the Cast section reads as the same surface.
// ---------------------------------------------------------------------------

const inputStyle: CSSProperties = {
  background: "var(--c-surface-2, transparent)",
  border: "1px solid var(--c-hairline-soft)",
  borderRadius: 6,
  padding: "5px 9px",
  fontFamily: "var(--c-font-mono)",
  fontSize: 12,
  color: "var(--c-ink)",
  boxSizing: "border-box",
  width: "100%",
};

const selectStyle: CSSProperties = {
  background: "var(--c-surface-2, transparent)",
  border: "1px solid var(--c-hairline-soft)",
  borderRadius: 6,
  padding: "4px 8px",
  fontSize: 12,
  color: "var(--c-ink)",
  maxWidth: "100%",
};

function saveButtonStyle(pending: boolean): CSSProperties {
  return {
    background: "var(--c-ink)",
    color: "var(--c-surface)",
    border: "none",
    borderRadius: 6,
    padding: "5px 14px",
    fontSize: 11.5,
    fontWeight: 600,
    cursor: pending ? "default" : "pointer",
    opacity: pending ? 0.6 : 1,
  };
}

// A quiet, secondary action button: same shape as the save button but reading
// as an outline against the paper, for the avatar/variant generators and links.
function ghostButtonStyle(pending: boolean): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    background: "var(--c-surface-2, transparent)",
    color: "var(--c-ink)",
    border: "1px solid var(--c-hairline-soft)",
    borderRadius: 6,
    padding: "5px 11px",
    fontSize: 11.5,
    fontWeight: 600,
    cursor: pending ? "default" : "pointer",
    opacity: pending ? 0.6 : 1,
    textDecoration: "none",
  };
}

// The three governance verdicts, mapped to the small chip treatment. Mirrors the
// badge logic the v1 panel uses, recast into the v2 chip idiom (a tinted pill).
const GOVERNANCE_BADGE: Record<GovernanceStatus, { label: string; color: string; background: string }> = {
  on_brand: { label: "On-brand", color: "#5b7d4b", background: "rgba(120,170,90,0.14)" },
  needs_review: { label: "Needs review", color: "#9a6a1f", background: "rgba(220,160,40,0.14)" },
  unrated: { label: "Unrated", color: "var(--c-ink-3)", background: "var(--c-surface-2, transparent)" },
};

/**
 * Save one agent image (the avatar, or one of its state variants) to disk.
 * Goes through lib/download so an expired oauth2-proxy session raises instead of
 * saving an HTML login page named `iris-avatar.png`.
 */
function AvatarDownload({
  url,
  stem,
  label,
  text,
}: {
  url: string | null | undefined;
  stem: string;
  label: string;
  text: string;
}) {
  const [busy, setBusy] = useState(false);
  if (!url) return null;
  return (
    <button
      type="button"
      disabled={busy}
      aria-label={label}
      title={label}
      onClick={async () => {
        setBusy(true);
        try {
          await downloadFile(url, fileNameFor(stem, url));
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Download failed.");
        } finally {
          setBusy(false);
        }
      }}
      style={{ ...ghostButtonStyle(busy), textTransform: "capitalize" }}
    >
      {busy ? <Spinner size={13} /> : <Download size={14} />}
      {text}
    </button>
  );
}

function GovernanceBadge({ status }: { status: GovernanceStatus }) {
  const badge = GOVERNANCE_BADGE[status];
  return (
    <span
      className="chip"
      style={{ color: badge.color, background: badge.background, borderColor: "transparent" }}
    >
      {badge.label}
    </span>
  );
}

/**
 * The brand's created agents, as cards in CrewPage's idiom: each on-brand
 * identity (avatar + persona) with a voice picker and preview, an avatar/variant
 * generator, and a manifest link. Self-contained: it loads the brand's agent
 * team and the shared voice catalog, and every write refreshes both this query
 * and CrewPage's aggregate team query.
 */
export function BrandCast({ slug, brandName }: { slug: string; brandName: string }) {
  const queryClient = useQueryClient();

  const teamQ = useQuery({
    queryKey: ["agent-team", slug],
    queryFn: ({ signal }) => getAgentTeam(slug, signal),
  });
  // The voice catalog is brand-agnostic; load it once and share across cards.
  const voicesQ = useQuery({
    queryKey: ["voices"],
    queryFn: ({ signal }) => listVoices(signal),
    staleTime: 300000,
  });
  const version = teamQ.dataUpdatedAt; // cache-buster so a regenerated avatar refreshes

  // Refresh both this brand's team and CrewPage's aggregate, so the Cast and the
  // page headline/stewards stay in step after any write.
  function refreshTeams() {
    void queryClient.invalidateQueries({ queryKey: ["agent-team", slug] });
    void queryClient.invalidateQueries({ queryKey: ["agent-teams", "all"] });
  }

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [bio, setBio] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createBrandAgent(slug, { name: name.trim(), role: role.trim(), bio: bio.trim() }),
    onSuccess: (agent) => {
      setName("");
      setRole("");
      setBio("");
      setOpen(false);
      refreshTeams();
      toast.success(`Created ${agent.name}`);
    },
    onError: () => toast.error("Could not create the agent. Try again."),
  });

  const genAvatar = useMutation({
    mutationFn: (agentSlug: string) => generateBrandAgentAvatar(slug, agentSlug),
    onSuccess: () => {
      refreshTeams();
      toast.success("Avatar generated");
    },
    onError: () => toast.error("Could not generate the avatar. Try again."),
  });

  const genVariants = useMutation({
    mutationFn: (agentSlug: string) => generateAgentVariants(slug, agentSlug),
    onSuccess: () => {
      refreshTeams();
      toast.success("Variant set generated");
    },
    onError: () => toast.error("Could not generate the variant set. Try again."),
  });

  const setVoice = useMutation({
    mutationFn: ({ agentSlug, voiceId }: { agentSlug: string; voiceId: string }) =>
      setBrandAgentVoice(slug, agentSlug, { voice_id: voiceId || null }),
    onSuccess: () => {
      refreshTeams();
      toast.success("Voice updated");
    },
    onError: () => toast.error("Could not update the voice. Try again."),
  });

  const team = teamQ.data;
  const agents = team?.agents ?? [];

  const custom = (voicesQ.data?.voices ?? []).filter((v) => v.kind === "custom");
  const standard = (voicesQ.data?.voices ?? []).filter((v) => v.kind !== "custom");

  return (
    <section>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 14 }}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          style={saveButtonStyle(false)}
        >
          {open ? "Close" : "New agent"}
        </button>
      </div>

      {open ? (
        <form
          className="voice-card"
          style={{ display: "grid", gap: 12, padding: 16, marginBottom: 18 }}
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim() && role.trim()) create.mutate();
          }}
        >
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
            <label style={{ display: "block" }}>
              <span className="microlabel" style={{ display: "block", marginBottom: 4 }}>Name</span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Iris"
                autoFocus
                style={inputStyle}
              />
            </label>
            <label style={{ display: "block" }}>
              <span className="microlabel" style={{ display: "block", marginBottom: 4 }}>Role</span>
              <input
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="brand director"
                style={inputStyle}
              />
            </label>
          </div>
          <label style={{ display: "block" }}>
            <span className="microlabel" style={{ display: "block", marginBottom: 4 }}>Bio (optional)</span>
            <input
              value={bio}
              onChange={(e) => setBio(e.target.value)}
              placeholder="What this agent does for the brand."
              style={inputStyle}
            />
          </label>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button
              type="submit"
              disabled={!name.trim() || !role.trim() || create.isPending}
              style={saveButtonStyle(!name.trim() || !role.trim() || create.isPending)}
            >
              {create.isPending ? "Creating…" : "Create agent"}
            </button>
            <button type="button" onClick={() => setOpen(false)} style={ghostButtonStyle(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {teamQ.isLoading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: "40px 0" }}>
          <Spinner size={24} />
        </div>
      ) : agents.length === 0 ? (
        <div className="voice-card" style={{ padding: "22px 20px", textAlign: "center" }}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--c-ink-2)", lineHeight: 1.6 }}>
            No agents yet for {brandName}. Create the first one, then generate its on-brand avatar.
          </p>
        </div>
      ) : (
        <div className="crew-cast" style={{ display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
          {agents.map((a: AgentIdentity) => {
            const generating = genAvatar.isPending && genAvatar.variables === a.slug;
            const generatingVariants = genVariants.isPending && genVariants.variables === a.slug;
            const settingVoice = setVoice.isPending && setVoice.variables?.agentSlug === a.slug;
            const src = a.avatar_url ? `${a.avatar_url}?v=${version}` : null;
            const variantStates = Object.entries(a.variants ?? {});
            const tones = a.voice_tone ?? [];
            const currentVoice = a.voice?.voice_id ?? "";
            return (
              <div key={a.slug} className="voice-card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 13, alignItems: "flex-start" }}>
                  <AgentAvatar name={a.name} src={src} size={48} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 8 }}>
                      <b style={{ fontFamily: "var(--c-font-display)", fontSize: 15.5 }}>{a.name}</b>
                      <GovernanceBadge status={a.governance_status} />
                    </div>
                    <div className="microlabel" style={{ color: "#b9a7ff", marginTop: 2 }}>{a.role}</div>
                  </div>
                </div>

                {a.bio ? (
                  <p style={{ margin: 0, fontSize: 12.5, color: "var(--c-ink-2)", lineHeight: 1.55 }}>{a.bio}</p>
                ) : null}

                {tones.length > 0 ? (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {tones.slice(0, 5).map((t) => (
                      <span key={t} className="chip">{t}</span>
                    ))}
                  </div>
                ) : null}

                <AgentVoiceRow
                  agent={a}
                  currentVoice={currentVoice}
                  custom={custom}
                  standard={standard}
                  catalogLoaded={Boolean(voicesQ.data)}
                  previewAvailable={Boolean(voicesQ.data?.preview_available)}
                  settingVoice={settingVoice}
                  onChange={(voiceId) => setVoice.mutate({ agentSlug: a.slug, voiceId })}
                />

                {variantStates.length > 0 ? (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
                    {variantStates.map(([state, url]) => (
                      <div key={state} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                        <AgentAvatar name={a.name} src={`${url}?v=${version}`} size={40} />
                        <span style={{ fontSize: 10.5, textTransform: "capitalize", color: "var(--c-ink-3)" }}>{state}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: "auto" }}>
                  <button
                    type="button"
                    onClick={() => genAvatar.mutate(a.slug)}
                    disabled={generating}
                    style={ghostButtonStyle(generating)}
                  >
                    <Sparkles size={14} />
                    {generating ? "Generating…" : a.avatar_url ? "Regenerate avatar" : "Generate avatar"}
                  </button>
                  <button
                    type="button"
                    onClick={() => genVariants.mutate(a.slug)}
                    disabled={generatingVariants}
                    style={ghostButtonStyle(generatingVariants)}
                  >
                    <Layers size={14} />
                    {generatingVariants ? "Generating…" : "Generate variant set"}
                  </button>
                  {/* Retrieval: the card showed the avatar (and its variant
                      set) with no way to save any of them. "Manifest" was the
                      only Download-icon button here, and it links to JSON. */}
                  <AvatarDownload
                    url={a.avatar_url}
                    stem={`${slug}-${a.slug}-avatar`}
                    label={`Download ${a.name}'s avatar`}
                    text="Avatar"
                  />
                  {variantStates.map(([state, url]) => (
                    <AvatarDownload
                      key={`dl-${state}`}
                      url={url}
                      stem={`${slug}-${a.slug}-${state}`}
                      label={`Download ${a.name}'s ${state} avatar`}
                      text={state}
                    />
                  ))}
                  <a
                    href={agentManifestUrl(slug, a.slug)}
                    target="_blank"
                    rel="noreferrer"
                    style={ghostButtonStyle(false)}
                  >
                    <FileJson size={14} />
                    Manifest
                  </a>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

/**
 * One agent card's voice row: the bound voice (or none) in a picker that assigns
 * from the catalog on change, plus a preview button that plays a short sample
 * when an orator is wired. Brand-Ops owns the voice; Stable/orator speak it.
 */
function AgentVoiceRow({
  agent,
  currentVoice,
  custom,
  standard,
  catalogLoaded,
  previewAvailable,
  settingVoice,
  onChange,
}: {
  agent: AgentIdentity;
  currentVoice: string;
  custom: { id: string; label: string; descriptor: string | null }[];
  standard: { id: string; label: string; descriptor: string | null }[];
  catalogLoaded: boolean;
  previewAvailable: boolean;
  settingVoice: boolean;
  onChange: (voiceId: string) => void;
}) {
  const [previewing, setPreviewing] = useState(false);

  async function preview() {
    const voiceId = agent.voice?.voice_id;
    if (!voiceId) return;
    setPreviewing(true);
    try {
      const blob = await previewVoice({ voice_id: voiceId });
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

  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
      <span className="microlabel" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
        <Volume2 size={12} style={{ color: "#b9a7ff" }} />
        Voice
      </span>
      <select
        value={currentVoice}
        disabled={settingVoice || !catalogLoaded}
        onChange={(e) => onChange(e.target.value)}
        style={selectStyle}
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
        {standard.length > 0 ? (
          <optgroup label="Standard">
            {standard.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
                {v.descriptor ? ` - ${v.descriptor}` : ""}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
      {previewAvailable && agent.voice?.voice_id ? (
        <button
          type="button"
          onClick={preview}
          disabled={previewing}
          style={ghostButtonStyle(previewing)}
        >
          <Volume2 size={14} />
          {previewing ? "Playing…" : "Preview"}
        </button>
      ) : null}
    </div>
  );
}
