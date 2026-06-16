/**
 * The ⌘K command palette (cmdk). Table-stakes for a 2026 tool: run a primary
 * action (new brand, open the brand book, the review queue, new from template),
 * fuzzy-jump to any section, or switch the active brand. Opens on Cmd/Ctrl-K,
 * closes on Escape. The active row is tinted with the brand accent. Mounted once
 * in the Layout, above the routed content.
 *
 * Two niceties on top of that. When the search box is empty we surface a
 * "Recent" group at the very top (backed by lib/recents) so a returning user can
 * re-fire their last action or hop back to the brand they were just in with one
 * keystroke; it hides the moment they type, leaving cmdk to fuzzy-filter the
 * normal groups. And a slim footer hint bar (Raycast/Linear style) shows the
 * keyboard moves and the active brand.
 */
import { useEffect, useState } from "react";
import { Command } from "cmdk";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  Sparkles,
  LibraryBig,
  LayoutTemplate,
  Palette,
  Check,
  Plus,
  BookOpen,
  ClipboardCheck,
  History,
} from "lucide-react";
import { useActiveBrand } from "../brand-context";
import { spring } from "../lib/motion";
import { getRecents, pushRecent, type RecentEntry } from "../lib/recents";

const SECTIONS = [
  { to: "/", label: "Brands", icon: Palette },
  { to: "/library", label: "Library", icon: LibraryBig },
] as const;

// Shared row classes, so the Recent rows match the rest exactly.
const ITEM_CLASS =
  "flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text";
const GROUP_CLASS =
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-text-faint";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  // The live search text, so we can tell when the box is empty (Recent group
  // only shows then). cmdk owns filtering; we just mirror the value.
  const [search, setSearch] = useState("");
  // Snapshot of recents, read when the palette opens so a just-used item is
  // there next time without waiting on a remount.
  const [recents, setRecents] = useState<RecentEntry[]>([]);
  const navigate = useNavigate();
  const { brands, activeSlug, activeBrand, setActiveSlug } = useActiveBrand();
  const currentBrand = activeSlug ?? brands[0]?.slug ?? "magic-unicorn";

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const k = e.key.toLowerCase();
      if (k === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // On open: refresh the recents snapshot and clear any stale search text.
  useEffect(() => {
    if (open) {
      setRecents(getRecents());
      setSearch("");
    }
  }, [open]);

  function go(to: string) {
    navigate(to);
    setOpen(false);
  }

  // The recordable actions, keyed by a stable id. Each runs its side-effect and
  // remembers itself. Brand switches are recorded separately (one id per brand).
  // runRecent() re-dispatches by the entry's kind+id so a Recent row fires the
  // exact same handler (preserving the sessionStorage handoffs) and re-records.
  function openBrandBook() {
    if (!activeSlug) return;
    pushRecent({ kind: "action", id: "open-brand-book", label: "Open brand book" });
    go(`/b/${activeSlug}/book`);
  }
  function openStudio() {
    pushRecent({ kind: "action", id: "open-studio", label: "Open Studio" });
    go(`/b/${currentBrand}/studio`);
  }
  function newFromTemplate() {
    pushRecent({ kind: "action", id: "new-from-template", label: "New from template" });
    go(`/b/${currentBrand}/studio?template=1`);
  }
  function goSection(to: string, label: string) {
    pushRecent({ kind: "action", id: `go:${to}`, label, to });
    go(to);
  }
  function switchBrand(slug: string, displayName: string) {
    pushRecent({ kind: "brand", id: slug, label: displayName, brandSlug: slug });
    setActiveSlug(slug);
    go(`/b/${slug}/overview`);
  }

  // Start a distinct new brand: the Studio reads this handoff on mount and opens
  // its intake panel in "distinct" mode (the same handoff the Brands page sets).
  function newBrand() {
    pushRecent({ kind: "action", id: "new-brand", label: "New brand" });
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem("brand-ops.new-brand-mode", "distinct");
    }
    go("/brands/new");
  }

  // Open the review queue: the Library reads this handoff on mount and lands
  // filtered to the assets that need review.
  function reviewQueue() {
    pushRecent({ kind: "action", id: "review-queue", label: "Review queue" });
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem("brand-ops.library-status", "needs_review");
    }
    go("/library");
  }

  // Re-fire a Recent row through its source handler so side-effects and the
  // re-push stay identical to selecting the original item.
  function runRecent(entry: RecentEntry) {
    if (entry.kind === "brand" && entry.brandSlug) {
      switchBrand(entry.brandSlug, entry.label);
      return;
    }
    switch (entry.id) {
      case "new-brand":
        return newBrand();
      case "open-brand-book":
        return openBrandBook();
      case "open-studio":
        return openStudio();
      case "review-queue":
        return reviewQueue();
      case "new-from-template":
        return newFromTemplate();
      default:
        // A "go:<route>" section nav, or any future plain-navigation action.
        if (entry.to) goSection(entry.to, entry.label);
    }
  }

  const showRecent = search.trim() === "" && recents.length > 0;

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[100] grid place-items-start justify-center bg-black/50 px-4 pt-[16vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}
        >
          <motion.div
            onClick={(e) => e.stopPropagation()}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={spring.panel}
            className="w-full max-w-xl"
          >
            <Command
              label="Command menu"
              className="glass overflow-hidden rounded-2xl shadow-float"
            >
              <Command.Input
                autoFocus
                value={search}
                onValueChange={setSearch}
                placeholder="Run an action, or jump to a section or brand…"
                className="w-full border-b border-white/8 bg-transparent px-4 py-3.5 text-sm text-text outline-none placeholder:text-text-faint"
              />
              <Command.List className="max-h-80 overflow-auto p-2">
                <Command.Empty className="px-3 py-6 text-center text-sm text-text-faint">
                  No results.
                </Command.Empty>

                {showRecent && (
                  <Command.Group heading="Recent" className={GROUP_CLASS}>
                    {recents.map((r) => (
                      <Command.Item
                        // Distinct "recent ..." value so it never collides with
                        // its twin in Actions when cmdk shows everything (empty
                        // query). It's conditionally unmounted once the user
                        // types, so it can't pollute filtered results.
                        key={`${r.kind}:${r.id}`}
                        value={`recent ${r.kind} ${r.id}`}
                        onSelect={() => runRecent(r)}
                        className={ITEM_CLASS}
                      >
                        <History size={15} />
                        {r.label}
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}

                <Command.Group heading="Actions" className={GROUP_CLASS}>
                  <Command.Item value="action new brand" onSelect={newBrand} className={ITEM_CLASS}>
                    <Plus size={15} />
                    New brand
                  </Command.Item>
                  {activeSlug && (
                    <Command.Item
                      value="action open brand book guidelines"
                      onSelect={openBrandBook}
                      className={ITEM_CLASS}
                    >
                      <BookOpen size={15} />
                      Open brand book
                    </Command.Item>
                  )}
                  <Command.Item
                    value="action open active brand studio generate"
                    onSelect={openStudio}
                    className={ITEM_CLASS}
                  >
                    <Sparkles size={15} />
                    Open Studio
                  </Command.Item>
                  <Command.Item
                    value="action review queue needs review"
                    onSelect={reviewQueue}
                    className={ITEM_CLASS}
                  >
                    <ClipboardCheck size={15} />
                    Review queue
                  </Command.Item>
                  <Command.Item
                    value="action new from template"
                    onSelect={newFromTemplate}
                    className={ITEM_CLASS}
                  >
                    <LayoutTemplate size={15} />
                    New from template
                  </Command.Item>
                </Command.Group>

                <Command.Group heading="Go to" className={GROUP_CLASS}>
                  {SECTIONS.map((s) => {
                    const Icon = s.icon;
                    return (
                      <Command.Item
                        key={s.to}
                        value={`go ${s.label}`}
                        onSelect={() => goSection(s.to, s.label)}
                        className={ITEM_CLASS}
                      >
                        <Icon size={15} />
                        {s.label}
                      </Command.Item>
                    );
                  })}
                </Command.Group>

                {brands.length > 0 && (
                  <Command.Group heading="Switch brand" className={GROUP_CLASS}>
                    {brands.map((b) => (
                      <Command.Item
                        key={b.slug}
                        value={`brand ${b.display_name} ${b.slug}`}
                        onSelect={() => switchBrand(b.slug, b.display_name)}
                        className={ITEM_CLASS}
                      >
                        <Palette size={15} />
                        <span className="flex-1">{b.display_name}</span>
                        {b.slug === activeSlug && <Check size={14} className="text-accent" />}
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
              </Command.List>

              {/* Raycast-style hint bar. A border-t hairline mirroring the
                  input's border-b; decorative kbd chips for the moves cmdk
                  already handles, plus the active brand on the left. Sits below
                  the scrolling list so it stays pinned and never clips the
                  panel's rounded corners. */}
              <div className="flex items-center justify-between gap-3 border-t border-white/8 px-3 py-2 text-xs text-text-faint">
                <span className="truncate">
                  {activeBrand ? `Active: ${activeBrand.display_name}` : "No brand selected"}
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="flex items-center gap-1">
                    <kbd className="rounded bg-white/8 px-1.5 py-0.5 font-sans text-text-muted">↑</kbd>
                    <kbd className="rounded bg-white/8 px-1.5 py-0.5 font-sans text-text-muted">↓</kbd>
                    navigate
                  </span>
                  <span className="flex items-center gap-1">
                    <kbd className="rounded bg-white/8 px-1.5 py-0.5 font-sans text-text-muted">↵</kbd>
                    select
                  </span>
                  <span className="flex items-center gap-1">
                    <kbd className="rounded bg-white/8 px-1.5 py-0.5 font-sans text-text-muted">esc</kbd>
                    close
                  </span>
                </span>
              </div>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
