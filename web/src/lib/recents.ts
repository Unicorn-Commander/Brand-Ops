/**
 * Recently-used command-palette entries, persisted in localStorage.
 *
 * The ⌘K palette surfaces a "Recent" group at the top when the search box is
 * empty, so a returning user can re-fire their last action or jump back to the
 * brand they were just in with a single keystroke. This module is the storage
 * behind that: a small, capped, de-duplicated, most-recent-first list. It owns
 * no UI and no React; it just reads and writes the list. The palette records an
 * entry whenever an action runs or a brand is switched, and reads the list back
 * when it opens.
 *
 * Entries are stored most-recent-first, de-duplicated by `kind + id` (so re-
 * running the same action moves it to the front rather than adding a copy), and
 * capped to MAX so the group stays a short-list, not a history log.
 */

/** The shape of one remembered entry. */
export interface RecentEntry {
  /** Whether this was a palette action or a brand switch. */
  kind: "action" | "brand";
  /** Stable id within the kind (the action's key, or the brand slug). */
  id: string;
  /** What to show in the row. */
  label: string;
  /** A route to navigate to, for entries that are a plain navigation. */
  to?: string;
  /** The brand to make active, for brand-switch entries. */
  brandSlug?: string;
}

/** Namespaced so it sits alongside the app's other localStorage keys. */
const STORAGE_KEY = "brand-ops.command-recents";

/** Keep the group a short-list, not a log. */
const MAX = 5;

/** Read the saved list, most-recent-first. Returns [] if storage is unavailable or empty. */
export function getRecents(): RecentEntry[] {
  if (typeof localStorage === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // Be defensive: a hand-edited or stale value shouldn't crash the palette.
    return Array.isArray(parsed) ? (parsed as RecentEntry[]) : [];
  } catch {
    return [];
  }
}

/**
 * Record an entry as the most recent: drop any existing entry with the same
 * kind+id, unshift this one to the front, and cap the list to MAX.
 */
export function pushRecent(entry: RecentEntry): void {
  if (typeof localStorage === "undefined") return;
  const next = [
    entry,
    ...getRecents().filter((e) => !(e.kind === entry.kind && e.id === entry.id)),
  ].slice(0, MAX);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage full or blocked (private mode): recents are a convenience, so
    // silently skip rather than break the action the user actually wanted.
  }
}
