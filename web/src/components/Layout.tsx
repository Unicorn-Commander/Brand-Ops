import { useState, useRef, useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";
import {
  Sparkles,
  LibraryBig,
  Palette,
  Workflow,
  ChevronDown,
  Check,
} from "lucide-react";
import { useActiveBrand } from "../brand-context";
import { BRAND_ACCENTS } from "../theme";

/** The four primary sections, in nav order. */
const SECTIONS = [
  { to: "/", label: "Studio", icon: Sparkles, end: true },
  { to: "/library", label: "Library", icon: LibraryBig, end: false },
  { to: "/brands", label: "Brands", icon: Palette, end: false },
  { to: "/workflows", label: "Workflows", icon: Workflow, end: false },
] as const;

function navClass({ isActive }: { isActive: boolean }): string {
  const base =
    "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-chrome-raised text-text`
    : `${base} text-text-muted hover:text-text hover:bg-chrome-hover`;
}

/** A small color swatch for a brand, using its accent pairing when known. */
function BrandSwatch({ slug }: { slug: string }) {
  const pair = BRAND_ACCENTS[slug];
  const gradient = pair
    ? `linear-gradient(135deg, ${pair.primary}, ${pair.secondary})`
    : "linear-gradient(135deg, #b66eff, #00d4ff)";
  return (
    <span
      className="h-3.5 w-3.5 shrink-0 rounded-full ring-1 ring-white/10"
      style={{ background: gradient }}
    />
  );
}

function BrandSwitcher() {
  const { brands, activeBrand, activeSlug, setActiveSlug, brandsLoading } =
    useActiveBrand();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label = activeBrand?.display_name ?? (brandsLoading ? "Loading" : "Brand");

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="inline-flex items-center gap-2 rounded-lg border border-chrome-border bg-chrome-raised px-3 py-1.5 text-sm text-text hover:bg-chrome-hover"
      >
        {activeSlug && <BrandSwatch slug={activeSlug} />}
        <span className="max-w-[10rem] truncate">{label}</span>
        <ChevronDown size={15} className="text-text-muted" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute right-0 z-50 mt-2 max-h-80 w-60 overflow-auto rounded-xl border border-chrome-border bg-chrome-panel p-1 shadow-panel"
        >
          {brands.length === 0 && (
            <li className="px-3 py-2 text-sm text-text-muted">
              {brandsLoading ? "Loading brands" : "No brands found"}
            </li>
          )}
          {brands.map((b) => {
            const selected = b.slug === activeSlug;
            return (
              <li key={b.slug} role="option" aria-selected={selected}>
                <button
                  type="button"
                  onClick={() => {
                    setActiveSlug(b.slug);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors ${
                    selected
                      ? "bg-chrome-raised text-text"
                      : "text-text-muted hover:bg-chrome-hover hover:text-text"
                  }`}
                >
                  <BrandSwatch slug={b.slug} />
                  <span className="min-w-0 flex-1 truncate">
                    {b.display_name}
                  </span>
                  {selected && <Check size={15} className="text-accent" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * The app chrome: a fixed top nav with the four sections and the brand
 * switcher, over a routed content outlet. The shell is constant; the active
 * brand themes the content via the accent CSS vars.
 */
export function Layout() {
  return (
    <div className="flex h-full flex-col bg-chrome-bg">
      <header className="sticky top-0 z-40 border-b border-chrome-border bg-chrome-surface/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <span
              className="h-6 w-6 rounded-md"
              style={{
                background:
                  "linear-gradient(135deg, rgb(var(--accent)), rgb(var(--accent-secondary)))",
              }}
            />
            <span className="text-sm font-semibold tracking-tight text-text">
              Brand-Ops
            </span>
          </div>

          <nav className="flex items-center gap-1">
            {SECTIONS.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={navClass}>
                <Icon size={16} />
                {label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto">
            <BrandSwitcher />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
