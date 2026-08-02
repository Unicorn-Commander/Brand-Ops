import { describe, it, expect } from "vitest";
import {
  activityDetail,
  activityTarget,
  buildArchitecture,
  displayActivity,
  inkAlpha,
  plural,
} from "./metrics";
import type { ActivityEvent } from "../lib/types";

function event(overrides: Partial<ActivityEvent> = {}): ActivityEvent {
  return {
    id: "e1",
    ts: "2026-08-01T22:53:57Z",
    actor: "iris",
    actor_kind: "agent",
    action: "generated",
    brand: "acme",
    target: "og_image/2026-08-01-22-53-57-og_image.png",
    note: null,
    ...overrides,
  };
}

describe("activityDetail", () => {
  // Regression: every activity surface rendered `<b>{actor}</b> {displayActivity(e)}`
  // and displayActivity ALSO prefixed the actor, so each row read
  // "iris  iris generated · ...". activityDetail must never contain the actor.
  it("omits the actor so a caller can render it separately", () => {
    const detail = activityDetail(event());
    expect(detail).not.toContain("iris");
    expect(detail.startsWith("generated")).toBe(true);
  });

  it("still names the actor when used standalone", () => {
    expect(displayActivity(event())).toMatch(/^iris generated/);
  });

  it("falls back to a verb when the API sends none", () => {
    expect(activityDetail(event({ action: "", target: null }))).toBe("updated");
  });

  it("drops the separator when there is no target", () => {
    expect(activityDetail(event({ target: null }))).toBe("generated");
  });
});

describe("activityTarget", () => {
  it("humanizes the asset type and strips the generation timestamp", () => {
    expect(activityTarget("og_image/2026-08-01-22-53-57-og_image.png")).toBe(
      "og image · og_image.png",
    );
  });

  it("leaves a target that is not a catalog path alone", () => {
    expect(activityTarget("brand.yaml")).toBe("brand.yaml");
    expect(activityTarget("https://example.com/a/b.png")).toBe(
      "https://example.com/a/b.png",
    );
  });

  it("is empty for a missing target", () => {
    expect(activityTarget(null)).toBe("");
    expect(activityTarget(undefined)).toBe("");
  });

  it("keeps a file name that carries no timestamp", () => {
    expect(activityTarget("og_image/majiks-studio-opengraph-1200x630.png")).toBe(
      "og image · majiks-studio-opengraph-1200x630.png",
    );
  });
});

describe("inkAlpha", () => {
  // Regression: the brand covers hardcoded rgba(255,255,255,·) for their
  // secondary inks. Project Ops and Retirement Leads both ship foreground
  // #171717 on background #fafaf9, so the tagline and asset counts were painted
  // white on a white card and vanished.
  it("derives the secondary ink from a DARK brand foreground, not white", () => {
    const got = inkAlpha("#171717", 0.66);
    expect(got).toBe("rgba(23,23,23,0.66)");
    expect(got).not.toContain("255,255,255");
  });

  it("keeps a light brand foreground light", () => {
    expect(inkAlpha("#f2f1ee", 0.5)).toBe("rgba(242,241,238,0.5)");
  });

  it("expands 3-digit hex", () => {
    expect(inkAlpha("#fff", 0.44)).toBe("rgba(255,255,255,0.44)");
  });

  it("falls back to white rather than NaN for an unparseable ink", () => {
    expect(inkAlpha("not-a-color", 0.66)).toBe("rgba(255,255,255,0.66)");
    expect(inkAlpha("", 0.66)).toBe("rgba(255,255,255,0.66)");
  });
});

describe("plural", () => {
  it("says 'asset' for one and 'assets' for any other count", () => {
    expect(plural(1, "asset")).toBe("1 asset");
    expect(plural(0, "asset")).toBe("0 assets");
    expect(plural(13, "asset")).toBe("13 assets");
  });

  it("takes an explicit plural for irregular nouns", () => {
    expect(plural(1, "entry", "entries")).toBe("1 entry");
    expect(plural(2, "entry", "entries")).toBe("2 entries");
  });
});

describe("buildArchitecture", () => {
  const brands = [
    { slug: "accounting-ops", parent: "unicorn-commander" },
    { slug: "magic-unicorn", parent: null },
    { slug: "majiks-dj", parent: "majiks-music-studio" },
    { slug: "majiks-music-studio", parent: "majiks-online" },
    { slug: "majiks-online", parent: "magic-unicorn" },
    { slug: "unicorn-commander", parent: "magic-unicorn" },
  ];

  function order(rows: Array<{ brand: { slug: string }; depth: number }>) {
    return rows.map((row) => `${"  ".repeat(row.depth)}${row.brand.slug}`);
  }

  // Regression: the page rendered [...roots, ...children] with one flat 34px
  // indent, so a grandchild sat at the same depth as a child and could appear
  // ABOVE its own parent.
  it("nests each brand under its parent, parents first", () => {
    const rows = buildArchitecture(brands);
    const lines = order(rows);
    expect(lines).toEqual([
      "magic-unicorn",
      "  majiks-online",
      "    majiks-music-studio",
      "      majiks-dj",
      "  unicorn-commander",
      "    accounting-ops",
    ]);
  });

  it("gives a grandchild a deeper indent than a child", () => {
    const byslug = new Map(
      buildArchitecture(brands).map((row) => [row.brand.slug, row.depth]),
    );
    expect(byslug.get("majiks-online")).toBe(1);
    expect(byslug.get("majiks-music-studio")).toBe(2);
    expect(byslug.get("majiks-dj")).toBe(3);
  });

  it("emits every brand exactly once", () => {
    const rows = buildArchitecture(brands);
    expect(rows).toHaveLength(brands.length);
    expect(new Set(rows.map((r) => r.brand.slug)).size).toBe(brands.length);
  });

  it("keeps a brand whose parent is not in the list, as a root", () => {
    const rows = buildArchitecture([{ slug: "orphan", parent: "gone" }]);
    expect(rows).toEqual([{ brand: { slug: "orphan", parent: "gone" }, depth: 0 }]);
  });

  it("terminates on a parent cycle instead of recursing forever", () => {
    const rows = buildArchitecture([
      { slug: "a", parent: "b" },
      { slug: "b", parent: "a" },
    ]);
    expect(rows.map((r) => r.brand.slug).sort()).toEqual(["a", "b"]);
  });
});
