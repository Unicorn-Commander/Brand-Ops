import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ViewMode = "simple" | "advanced";

const KEY = "brandops.viewmode";

function readInitial(): ViewMode {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "simple" || v === "advanced") return v;
  } catch {
    // localStorage can throw in private mode; fall through to the default.
  }
  return "simple";
}

interface ViewModeCtx {
  mode: ViewMode;
  setMode: (m: ViewMode) => void;
  isSimple: boolean;
  isAdvanced: boolean;
}

const Ctx = createContext<ViewModeCtx | null>(null);

/**
 * App-wide Simple/Advanced preference.
 *
 * This is NOT a feature gate: every feature is reachable in both modes (the
 * research is clear that a hard mode that HIDES features is a trap). Simple is
 * the default and the newcomer path: plain-language labels, advanced controls
 * collapsed, no raw technical identifiers. Advanced surfaces technical
 * identifiers (backend/model ids, token names) and expands dense controls for
 * power users. Persisted to localStorage (mirrors the `brandops.workspace`
 * pattern) and published to `body[data-viewmode]` so plain CSS can adapt without
 * threading the value everywhere.
 */
export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ViewMode>(readInitial);

  useEffect(() => {
    document.body.dataset.viewmode = mode;
    try {
      localStorage.setItem(KEY, mode);
    } catch {
      // Persistence is best-effort; the in-memory state still drives the UI.
    }
  }, [mode]);

  const value: ViewModeCtx = {
    mode,
    setMode: setModeState,
    isSimple: mode === "simple",
    isAdvanced: mode === "advanced",
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** Read the active view mode. Falls back to "simple" outside a provider (so a
 *  component rendered in isolation, e.g. a test, still works). */
export function useViewMode(): ViewModeCtx {
  const ctx = useContext(Ctx);
  if (ctx) return ctx;
  return { mode: "simple", setMode: () => {}, isSimple: true, isAdvanced: false };
}
