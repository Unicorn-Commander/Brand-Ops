import { describe, it, expect } from "vitest";
import { readStudioDeepLink, strippedSearch } from "./deeplink";

describe("readStudioDeepLink", () => {
  // Regression: the Studio read ONLY `?prompt=`. Ten buttons across the app
  // (Brand HQ "Discuss ↗" x3 + "Discuss the brand", every missing kit cell's
  // "Generate →", the Mark page's "Ask why it looks this way" and "Commission
  // variant", the Portfolio's "Generate a kit") navigated here with context /
  // asset / pack and did nothing at all.
  it("turns ?context= into a real opening turn", () => {
    for (const context of ["overview", "color", "type", "voice", "mark"]) {
      const link = readStudioDeepLink(`?context=${context}`);
      expect(link.prompt, context).toBeTruthy();
      expect(link.prompt!.length, context).toBeGreaterThan(20);
    }
  });

  it("turns ?asset= into a turn AND a control preselect", () => {
    const link = readStudioDeepLink("?asset=launcher_tile");
    expect(link.asset).toBe("launcher_tile");
    expect(link.prompt).toBe("Make a launcher tile for this brand.");
  });

  it("turns ?pack= into a turn", () => {
    const link = readStudioDeepLink("?pack=core");
    expect(link.pack).toBe("core");
    expect(link.prompt).toBe("Generate the core pack for this brand.");
  });

  it("lets the operator's own words win over a derived one", () => {
    const link = readStudioDeepLink("?prompt=make%20me%20a%20logo&context=color");
    expect(link.prompt).toBe("make me a logo");
  });

  it("invents nothing for an unknown context", () => {
    expect(readStudioDeepLink("?context=nonsense").prompt).toBeNull();
  });

  it("is inert with no parameters", () => {
    expect(readStudioDeepLink("")).toEqual({ prompt: null, asset: null, pack: null });
    expect(readStudioDeepLink("?other=1").prompt).toBeNull();
  });

  it("treats a whitespace-only prompt as absent", () => {
    expect(readStudioDeepLink("?prompt=%20%20").prompt).toBeNull();
  });

  it("is case-insensitive on the context key", () => {
    expect(readStudioDeepLink("?context=COLOR").prompt).toBe(
      readStudioDeepLink("?context=color").prompt,
    );
  });
});

describe("strippedSearch", () => {
  // A refresh must not resend the opening turn.
  it("removes every consumed parameter", () => {
    expect(strippedSearch("?prompt=hi&context=color&asset=logo&pack=core")).toBe("");
  });

  it("keeps parameters it does not own", () => {
    expect(strippedSearch("?context=color&status=needs_review")).toBe(
      "?status=needs_review",
    );
  });

  it("is empty for an empty search", () => {
    expect(strippedSearch("")).toBe("");
  });
});
