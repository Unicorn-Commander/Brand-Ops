import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { intakeBrand, listBrands } from "../../api/client";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";

const MODES = [
  { id: "describe", label: "From nothing" },
  { id: "onboard", label: "From a brand" },
] as const;

export default function NewBrandPage() {
  const brandsQ = useQuery({ queryKey: ["brands"], queryFn: ({ signal }) => listBrands(signal) });
  const [mode, setMode] = useState<"describe" | "onboard">("describe");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [parent, setParent] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: async () =>
      intakeBrand({
        name,
        description,
        url: url || null,
        parent: parent || null,
        mode,
      }),
  });

  if (brandsQ.isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "80px 0" }}>
        <Spinner size={28} />
      </div>
    );
  }

  if (!brandsQ.data) {
    return <EmptyState title="Brand intake unavailable" description="Could not load the brand list." />;
  }

  return (
    <div className="rise" style={{ padding: "40px 0 60px" }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, flexWrap: "wrap" }}>
        <div>
          <span className="microlabel">New brand</span>
          <h1 style={{ margin: "8px 0 0", fontFamily: "var(--b-font-display)", fontSize: 38, letterSpacing: "-0.025em" }}>
            The intake run
          </h1>
          <p className="annotation" style={{ marginTop: 8, maxWidth: 760 }}>
            The Director drafts the identity, then writes the actual brand record. You only need to supply the brief.
          </p>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          {MODES.map((item) => (
            <button key={item.id} type="button" className={`btn ${mode === item.id ? "btn-ink" : "btn-ghost"}`} onClick={() => setMode(item.id)}>
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 22, marginTop: 28 }}>
        <form
          className="voice-card"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
        >
          <span className="microlabel">Brief</span>
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Tidepool Labs" style={inputStyle} />
          </label>
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Description</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A personal health-data vault. Calm trust, on their shore."
              rows={4}
              style={{ ...inputStyle, resize: "vertical" }}
            />
          </label>
          {mode === "onboard" && (
            <label style={{ display: "block", marginTop: 12 }}>
              <span className="microlabel">Website URL</span>
              <input value={url} onChange={(e) => setUrl(e.target.value)} style={inputStyle} />
            </label>
          )}
          <label style={{ display: "block", marginTop: 12 }}>
            <span className="microlabel">Parent</span>
            <select value={parent ?? ""} onChange={(e) => setParent(e.target.value || null)} style={inputStyle}>
              <option value="">None</option>
              {brandsQ.data.map((brand) => (
                <option key={brand.slug} value={brand.slug}>
                  {brand.display_name}
                </option>
              ))}
            </select>
          </label>
          <button className="btn btn-accent" type="submit" style={{ marginTop: 16 }}>
            Stage 4
          </button>
          {mutation.isError && (
            <p style={{ color: "var(--c-stop)", marginTop: 12 }}>
              {mutation.error instanceof Error ? mutation.error.message : "Intake failed."}
            </p>
          )}
          {mutation.data && (
            <div style={{ marginTop: 16, border: "1px solid var(--b-hairline)", borderRadius: "var(--radius-sm)", padding: 14 }}>
              <b>{mutation.data.brand.display_name}</b>
              <p style={{ color: "var(--b-ink-2)", margin: "6px 0 0" }}>{mutation.data.draft.rationale}</p>
              <div style={{ color: "var(--b-ink-3)", fontSize: 12, marginTop: 8 }}>
                {mutation.data.notes.join(" · ")}
              </div>
            </div>
          )}
        </form>

        <section className="voice-card">
          <span className="microlabel">Pipeline</span>
          <div style={{ display: "grid", gap: 14, marginTop: 14 }}>
            {[
              ["01", "Brief", "done"],
              ["02", "Research", "done"],
              ["03", "Directions", "drafting"],
              ["04", "Ratify", "your move"],
              ["05", "Kit + steward", "queued"],
              ["06", "Live + on watch", "queued"],
            ].map(([no, title, status], index) => (
              <div
                key={no}
                style={{
                  border: index === 3 ? "1px dashed var(--b-hairline)" : "1px solid var(--b-hairline)",
                  borderRadius: "var(--radius-sm)",
                  padding: 14,
                  background: index === 2 ? "rgba(155,140,255,0.08)" : "rgba(255,255,255,0.02)",
                }}
              >
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span className="chip">{no}</span>
                  <b>{title}</b>
                  <span style={{ marginLeft: "auto", color: index === 3 ? "var(--c-warn)" : "var(--b-ink-3)", fontSize: 11 }}>{status}</span>
                </div>
                <p style={{ color: "var(--b-ink-3)", margin: "8px 0 0" }}>
                  {index === 3 ? "The only human gate in the run." : index < 3 ? "Live data already on record." : "Will materialize after ratify."}
                </p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

const inputStyle = {
  width: "100%",
  marginTop: 6,
  borderRadius: 10,
  border: "1px solid var(--b-hairline)",
  background: "rgba(255,255,255,0.03)",
  color: "var(--b-ink)",
  padding: "10px 12px",
  font: "inherit",
} as const;
