import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { pushRecent, getRecents, type RecentEntry } from "./recents";

// jsdom 25 only exposes window.localStorage when Node is started with
// --localstorage-file, which vitest does not set, so it lands undefined here.
// Install a minimal, spec-shaped in-memory Storage so these tests exercise the
// real read/write path the module takes (it guards on `typeof localStorage`,
// then calls getItem/setItem). Test-harness only; the module is untouched.
function installMemoryLocalStorage(): void {
  if (typeof globalThis.localStorage !== "undefined") return;
  const store = new Map<string, string>();
  const memory: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v));
    },
    removeItem: (k: string) => {
      store.delete(k);
    },
    key: (i: number) => Array.from(store.keys())[i] ?? null,
  };
  Object.defineProperty(globalThis, "localStorage", {
    value: memory,
    configurable: true,
    writable: true,
  });
}

function entry(id: string, kind: RecentEntry["kind"] = "action"): RecentEntry {
  return { kind, id, label: `${kind}:${id}` };
}

describe("recents", () => {
  beforeAll(() => {
    installMemoryLocalStorage();
  });

  beforeEach(() => {
    // Reset the shared store between cases.
    localStorage.clear();
  });

  it("starts empty", () => {
    expect(getRecents()).toEqual([]);
  });

  it("stores most-recent-first", () => {
    pushRecent(entry("a"));
    pushRecent(entry("b"));
    pushRecent(entry("c"));
    expect(getRecents().map((e) => e.id)).toEqual(["c", "b", "a"]);
  });

  it("caps the list at 5", () => {
    for (const id of ["a", "b", "c", "d", "e", "f", "g"]) {
      pushRecent(entry(id));
    }
    const ids = getRecents().map((e) => e.id);
    expect(ids).toHaveLength(5);
    // Newest five kept, oldest dropped, newest first.
    expect(ids).toEqual(["g", "f", "e", "d", "c"]);
  });

  it("dedupes by kind+id, moving a re-push to the front without a copy", () => {
    pushRecent(entry("a"));
    pushRecent(entry("b"));
    pushRecent(entry("a")); // re-push the existing 'a'
    const ids = getRecents().map((e) => e.id);
    expect(ids).toEqual(["a", "b"]);
    // exactly one 'a'
    expect(ids.filter((id) => id === "a")).toHaveLength(1);
  });

  it("treats the same id under a different kind as a distinct entry", () => {
    pushRecent(entry("x", "action"));
    pushRecent(entry("x", "brand"));
    const rows = getRecents();
    expect(rows).toHaveLength(2);
    expect(rows.map((e) => `${e.kind}:${e.id}`)).toEqual(["brand:x", "action:x"]);
  });

  it("recovers from a corrupt stored value by returning []", () => {
    localStorage.setItem("brand-ops.command-recents", "{not json");
    expect(getRecents()).toEqual([]);
  });
});
