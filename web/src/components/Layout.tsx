import { useState, useRef, useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import {
  Sparkles,
  LibraryBig,
  LayoutTemplate,
  Palette,
  Workflow,
  Activity,
  ChevronDown,
  Check,
  Command as CommandIcon,
} from "lucide-react";
import { useActiveBrand } from "../brand-context";
import { BRAND_ACCENTS } from "../theme";
import { AuroraBackground } from "./AuroraBackground";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { pageVariants } from "../lib/motion";

/** The primary sections, in nav order. Brands-first: the Showcase is home. */
const SECTIONS = [
  { to: "/", label: "Brands", icon: Palette, end: true },
  { to: "/studio", label: "Studio", icon: Sparkles, end: false },
  { to: "/library", label: "Library", icon: LibraryBig, end: false },
  { to: "/templates", label: "Templates", icon: LayoutTemplate, end: false },
  { to: "/workflows", label: "Workflows", icon: Workflow, end: false },
  { to: "/activity", label: "Activity", icon: Activity, end: false },
] as const;

function navClass({ isActive }: { isActive: boolean }): string {
  const base =
    "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors";
  return isActive
    ? `${base} bg-white/10 text-text`
    : `${base} text-text-muted hover:text-text hover:bg-white/[0.06]`;
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
        className="inline-flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm text-text hover:bg-white/[0.08]"
      >
        {activeSlug && <BrandSwatch slug={activeSlug} />}
        <span className="max-w-[10rem] truncate">{label}</span>
        <ChevronDown size={15} className="text-text-muted" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="glass absolute right-0 z-50 mt-2 max-h-80 w-60 overflow-auto rounded-xl p-1 shadow-float"
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
                      ? "bg-white/10 text-text"
                      : "text-text-muted hover:bg-white/[0.06] hover:text-text"
                  }`}
                >
                  <BrandSwatch slug={b.slug} />
                  <span className="min-w-0 flex-1 truncate">{b.display_name}</span>
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

/** A static ⌘K hint button; dispatches the same shortcut the palette listens for. */
function CommandHint() {
  return (
    <button
      type="button"
      onClick={() =>
        document.dispatchEvent(
          new KeyboardEvent("keydown", { key: "k", metaKey: true }),
        )
      }
      className="hidden items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-xs text-text-faint hover:text-text-muted sm:inline-flex"
      title="Command menu"
    >
      <CommandIcon size={13} /> K
    </button>
  );
}

/**
 * The app chrome: the living aurora backdrop, a floating glass top bar (sections,
 * ⌘K, brand switcher), the command palette, and a route-level transition around
 * the routed content. The shell is constant; the active brand themes everything
 * via the accent CSS vars the aurora and glass read.
 */
export function Layout() {
  const location = useLocation();

  return (
    <div className="relative flex min-h-full flex-col">
      <AuroraBackground />

      <header className="sticky top-0 z-40 border-b border-white/8 bg-chrome-bg/40 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <NavLink to="/" className="flex items-center gap-2">
            <span
              className="h-6 w-6 rounded-md shadow-glow"
              style={{
                background:
                  "linear-gradient(135deg, rgb(var(--accent)), rgb(var(--accent-secondary)))",
              }}
            />
            <span className="font-display text-sm font-semibold tracking-tight text-text">
              Brand-Ops
            </span>
          </NavLink>

          <nav className="hidden items-center gap-1 md:flex">
            {SECTIONS.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={navClass}>
                <Icon size={16} />
                {label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <CommandHint />
            <BrandSwitcher />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-7">
        <AnimatePresence mode="wait">
          <motion.div
            key={location.pathname}
            variants={pageVariants}
            initial="initial"
            animate="animate"
            exit="exit"
          >
            {/* Keyed by route: a render error on one page shows a recoverable
                panel instead of blanking the whole app, and navigating (or
                browser Back) remounts the boundary and clears it. */}
            <ErrorBoundary key={location.pathname}>
              <Outlet />
            </ErrorBoundary>
          </motion.div>
        </AnimatePresence>
      </main>

      <CommandPalette />
    </div>
  );
}
