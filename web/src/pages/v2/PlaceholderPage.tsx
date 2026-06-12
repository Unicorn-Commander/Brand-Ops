import { Link, useParams } from "react-router-dom";

export function PlaceholderPage({
  title,
  note,
}: {
  title: string;
  note: string;
}) {
  const { slug } = useParams();
  return (
    <section className="v2-head rise" style={{ minHeight: "60vh", alignItems: "center" }}>
      <div>
        <span className="microlabel">{slug ? `Brand · ${slug}` : "Brand-Ops"}</span>
        <h1>{title}</h1>
        <p className="annotation" style={{ maxWidth: 680 }}>{note}</p>
        <div style={{ display: "flex", gap: 10, marginTop: 24 }}>
          <Link className="btn btn-ink" to={slug ? `/b/${slug}/studio` : "/brands/new"}>
            Open Studio
          </Link>
          <Link className="btn btn-ghost" to={slug ? `/b/${slug}/overview` : "/"}>
            Back
          </Link>
        </div>
      </div>
    </section>
  );
}

export function StandardsPage() {
  return (
    <PlaceholderPage
      title="Standards"
      note="Grade board lands in Phase 4. This route is active now so the v2 IA is stable."
    />
  );
}

export function CrewPage() {
  return (
    <PlaceholderPage
      title="Staff, not features."
      note="Crew lands in Phase 4 from the agent registry, team endpoint, MCP manifest, and access logs."
    />
  );
}

export function NewBrandPage() {
  return (
    <PlaceholderPage
      title="New brand"
      note="The intake run lands in Phase 4. For now, use Studio to call the existing Director intake flow."
    />
  );
}

export function BrandSurfacePage({ title }: { title: string }) {
  return (
    <PlaceholderPage
      title={title}
      note="This v2 brand surface is scheduled for a later phase. The route is reserved and the existing capability remains reachable where already implemented."
    />
  );
}
