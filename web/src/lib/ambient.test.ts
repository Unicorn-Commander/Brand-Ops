import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { ambientEnabled, setAmbientEnabled, applyAmbient } from "./ambient";

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

describe("ambient preference", () => {
  beforeAll(() => {
    installMemoryLocalStorage();
  });

  beforeEach(() => {
    localStorage.clear();
    document.body.className = "";
  });

  it("defaults to on when nothing is stored", () => {
    expect(ambientEnabled()).toBe(true);
  });

  it("is off only when explicitly set off", () => {
    setAmbientEnabled(false);
    expect(ambientEnabled()).toBe(false);
    setAmbientEnabled(true);
    expect(ambientEnabled()).toBe(true);
  });

  it("toggles the no-ambient body class to match the stored preference", () => {
    setAmbientEnabled(false);
    applyAmbient();
    expect(document.body.classList.contains("no-ambient")).toBe(true);

    setAmbientEnabled(true);
    applyAmbient();
    expect(document.body.classList.contains("no-ambient")).toBe(false);
  });
});
