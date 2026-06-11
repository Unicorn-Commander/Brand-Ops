/**
 * Showcase — the home screen. A visual gallery of every brand with three views
 * (gallery / list / folder), an editorial metric strip, and a "needs attention"
 * worklist that turns coverage gaps into one-click routes into a brand. Reads
 * everything from useShowcaseData; view-toggling is pure client state.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  LayoutGrid,
  List,
  Folder,
  Network,
  Palette,
  Plus,
  Layers,
  Wand2,
  Globe,
} from "lucide-react";
import { GlassPanel, ViewToggle, type ViewOption } from "../../components";
import { EmptyState } from "../../components/EmptyState";
import { Spinner } from "../../components/Spinner";
import { useShowcaseData, type ShowcaseBrand } from "./data";
import { BrandGallery, BrandListView, BrandFolderView } from "./components/views";
import { ArchitectureView } from "./components/ArchitectureView";

type ViewMode = "gallery" | "list" | "folder" | "architecture";

const VIEW_OPTIONS: ViewOption<ViewMode>[] = [
  { id: "gallery", icon: LayoutGrid, label: "Gallery" },
  { id: "list", icon: List, label: "List" },
  { id: "folder", icon: Folder, label: "Folder" },
  { id: "architecture", icon: Network, label: "Architecture" },
];

const VIEW_KEY = "brand-ops.showcase-view";

/** Order brands so each sub-brand sits directly after its parent (divisions
 *  grouped under the organization they belong to). Roots keep their order. */
function orderByHierarchy(brands: ShowcaseBrand[]): ShowcaseBrand[] {
  const slugs = new Set(brands.map((b) => b.brand.slug));
  const roots = brands.filter((b) => !b.brand.parent || !slugs.has(b.brand.parent));
  const out: ShowcaseBrand[] = [];
  const seen = new Set<string>();
  for (const r of roots) {
    out.push(r);
    seen.add(r.brand.slug);
    for (const c of brands) {
      if (c.brand.parent === r.brand.slug) {
        out.push(c);
        seen.add(c.brand.slug);
      }
    }
  }
  for (const b of brands) if (!seen.has(b.brand.slug)) out.push(b);
  return out;
}

/** The three ways to start a new brand. Each hands the chosen mode to the
 *  Studio console (the agent-first surface), which frames the operation. */
const NEW_BRAND_MODES = [
  {
    mode: "consistent",
    icon: Layers,
    label: "Consistent sub-brand",
    hint: "Extend an existing brand into a division or product line",
  },
  {
    mode: "distinct",
    icon: Wand2,
    label: "Distinct new brand",
    hint: "Stand up a brand with its own identity",
  },
  {
    mode: "onboard",
    icon: Globe,
    label: "Onboard from a website",
    hint: "Capture an existing brand from its site",
  },
] as const;

function NewBrandMenu() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function start(mode: string) {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem("brand-ops.new-brand-mode", mode);
    }
    setOpen(false);
    navigate("/studio");
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-2 rounded-xl bg-accent px-3 py-2 text-sm font-medium text-chrome-bg shadow-glow transition-colors hover:bg-accent/90"
      >
        <Plus size={15} /> New brand
      </button>
      {open && (
        <div className="glass absolute right-0 z-50 mt-2 w-72 overflow-hidden rounded-xl p-1 shadow-float">
          {NEW_BRAND_MODES.map((m) => {
            const Icon = m.icon;
            return (
              <button
                key={m.mode}
                type="button"
                onClick={() => start(m.mode)}
                className="flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-white/[0.06]"
              >
                <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-accent/15 text-accent">
                  <Icon size={15} />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-text">{m.label}</span>
                  <span className="block text-xs text-text-faint">{m.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function ShowcasePage() {
  const data = useShowcaseData();
  const [view, setView] = useState<ViewMode>(() => {
    if (typeof localStorage === "undefined") return "gallery";
    return (localStorage.getItem(VIEW_KEY) as ViewMode) ?? "gallery";
  });

  function changeView(v: ViewMode) {
    setView(v);
    if (typeof localStorage !== "undefined") localStorage.setItem(VIEW_KEY, v);
  }

  return (
    <div className="space-y-6">
      {/* Header. */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-text-faint">
            <Palette size={13} className="text-accent" /> Brands
          </p>
          <h1 className="mt-1 font-display text-hero font-semibold tracking-tight text-text">
            Brands you <span className="accent-gradient">manage</span>
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-text-muted">
            Organizations, their divisions, and client brands, with the assets
            and coverage for each.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <NewBrandMenu />
          <ViewToggle value={view} options={VIEW_OPTIONS} onChange={changeView} />
        </div>
      </div>

      {data.isLoading ? (
        <div className="flex justify-center py-20">
          <Spinner size={28} />
        </div>
      ) : data.isError ? (
        <GlassPanel>
          <EmptyState
            title="Could not load brands"
            description="The API did not respond. Check that the Brand-Ops server is running."
          />
        </GlassPanel>
      ) : data.brands.length === 0 ? (
        <GlassPanel>
          <EmptyState title="No brands yet" description="Seed or onboard a brand to begin." />
        </GlassPanel>
      ) : (
        <>
          {/* The home is the brands, full stop. The cross-brand activity feed and
              the coverage worklist used to sit above the gallery and read as
              clutter; they now live on the Activity tab, so this view stays clean
              and visual. */}
          {view === "gallery" && <BrandGallery brands={orderByHierarchy(data.brands)} />}
          {view === "list" && <BrandListView brands={orderByHierarchy(data.brands)} />}
          {view === "folder" && <BrandFolderView brands={orderByHierarchy(data.brands)} />}
          {view === "architecture" && <ArchitectureView brands={data.brands} />}
        </>
      )}
    </div>
  );
}
