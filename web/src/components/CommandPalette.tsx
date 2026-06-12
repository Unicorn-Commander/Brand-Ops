/**
 * The ⌘K command palette (cmdk). Table-stakes for a 2026 tool: run a primary
 * action (new brand, open the brand book, the review queue, new from template),
 * fuzzy-jump to any section, or switch the active brand. Opens on Cmd/Ctrl-K,
 * closes on Escape. The active row is tinted with the brand accent. Mounted once
 * in the Layout, above the routed content.
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
} from "lucide-react";
import { useActiveBrand } from "../brand-context";
import { spring } from "../lib/motion";

const SECTIONS = [
  { to: "/", label: "Brands", icon: Palette },
  { to: "/library", label: "Library", icon: LibraryBig },
] as const;

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const { brands, activeSlug, setActiveSlug } = useActiveBrand();
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

  function go(to: string) {
    navigate(to);
    setOpen(false);
  }

  // Start a distinct new brand: the Studio reads this handoff on mount and opens
  // its intake panel in "distinct" mode (the same handoff the Brands page sets).
  function newBrand() {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem("brand-ops.new-brand-mode", "distinct");
    }
    go("/brands/new");
  }

  // Open the review queue: the Library reads this handoff on mount and lands
  // filtered to the assets that need review.
  function reviewQueue() {
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.setItem("brand-ops.library-status", "needs_review");
    }
    go("/library");
  }

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
                placeholder="Run an action, or jump to a section or brand…"
                className="w-full border-b border-white/8 bg-transparent px-4 py-3.5 text-sm text-text outline-none placeholder:text-text-faint"
              />
              <Command.List className="max-h-80 overflow-auto p-2">
                <Command.Empty className="px-3 py-6 text-center text-sm text-text-faint">
                  No results.
                </Command.Empty>

                <Command.Group
                  heading="Actions"
                  className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-text-faint"
                >
                  <Command.Item
                    value="action new brand"
                    onSelect={newBrand}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                  >
                    <Plus size={15} />
                    New brand
                  </Command.Item>
                  {activeSlug && (
                    <Command.Item
                      value="action open brand book guidelines"
                      onSelect={() => go(`/b/${activeSlug}/book`)}
                      className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                    >
                      <BookOpen size={15} />
                      Open brand book
                    </Command.Item>
                  )}
                  <Command.Item
                    value="action open active brand studio generate"
                    onSelect={() => go(`/b/${currentBrand}/studio`)}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                  >
                    <Sparkles size={15} />
                    Open Studio
                  </Command.Item>
                  <Command.Item
                    value="action review queue needs review"
                    onSelect={reviewQueue}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                  >
                    <ClipboardCheck size={15} />
                    Review queue
                  </Command.Item>
                  <Command.Item
                    value="action new from template"
                    onSelect={() => go(`/b/${currentBrand}/studio?template=1`)}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                  >
                    <LayoutTemplate size={15} />
                    New from template
                  </Command.Item>
                </Command.Group>

                <Command.Group
                  heading="Go to"
                  className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-text-faint"
                >
                  {SECTIONS.map((s) => {
                    const Icon = s.icon;
                    return (
                      <Command.Item
                        key={s.to}
                        value={`go ${s.label}`}
                        onSelect={() => go(s.to)}
                        className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                      >
                        <Icon size={15} />
                        {s.label}
                      </Command.Item>
                    );
                  })}
                </Command.Group>

                {brands.length > 0 && (
                  <Command.Group
                    heading="Switch brand"
                    className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:text-text-faint"
                  >
                    {brands.map((b) => (
                      <Command.Item
                        key={b.slug}
                        value={`brand ${b.display_name} ${b.slug}`}
                        onSelect={() => {
                          setActiveSlug(b.slug);
                          go(`/b/${b.slug}/overview`);
                        }}
                        className="flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-muted data-[selected=true]:bg-accent/15 data-[selected=true]:text-text"
                      >
                        <Palette size={15} />
                        <span className="flex-1">{b.display_name}</span>
                        {b.slug === activeSlug && <Check size={14} className="text-accent" />}
                      </Command.Item>
                    ))}
                  </Command.Group>
                )}
              </Command.List>
            </Command>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
