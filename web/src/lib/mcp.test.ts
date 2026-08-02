import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchMcpManifest, mcpManifestUrl, resourceCount, toolCount } from "./mcp";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("mcpManifestUrl", () => {
  // Regression: the Crew page fetched `/.well-known/mcps.json` on the CONSOLE
  // origin, where nothing serves it, so nginx's SPA fallback answered 200 with
  // index.html and the MCP door read "— tools" permanently.
  it("points at the mcp. sibling host in a deployment, not the console origin", () => {
    expect(mcpManifestUrl("brand-ops.magicunicorn.dev")).toBe(
      "https://mcp.brand-ops.magicunicorn.dev/.well-known/mcps.json",
    );
    expect(mcpManifestUrl("brand-ops.magicunicorn.dev")).not.toBe(
      "/.well-known/mcps.json",
    );
  });

  it("stays same-origin off a brand-ops host (local dev, previews)", () => {
    expect(mcpManifestUrl("localhost")).toBe("/.well-known/mcps.json");
    expect(mcpManifestUrl("127.0.0.1")).toBe("/.well-known/mcps.json");
  });
});

function stubFetch(res: Partial<Response> & { json?: () => Promise<unknown> }) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: new Headers({ "content-type": "application/json" }),
    json: async () => ({}),
    ...res,
  }));
}

describe("fetchMcpManifest", () => {
  it("reads a real manifest", async () => {
    stubFetch({ json: async () => ({ version: "0.29.2", tools: [1, 2, 3], resources: [1] }) });
    const manifest = await fetchMcpManifest();
    expect(toolCount(manifest)).toBe(3);
    expect(resourceCount(manifest)).toBe(1);
    expect(manifest?.version).toBe("0.29.2");
  });

  // The exact production failure: a 200 whose body is the SPA's index.html.
  // `res.ok` is true, so a naive reader treats it as a manifest.
  it("returns null for a 200 that is actually the SPA fallback HTML", async () => {
    stubFetch({
      headers: new Headers({ "content-type": "text/html" }),
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });
    await expect(fetchMcpManifest()).resolves.toBeNull();
  });

  it("returns null on a non-2xx", async () => {
    stubFetch({ ok: false, status: 404 });
    await expect(fetchMcpManifest()).resolves.toBeNull();
  });

  it("returns null when the host is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    await expect(fetchMcpManifest()).resolves.toBeNull();
  });
});

describe("toolCount / resourceCount", () => {
  // Null, never 0: "0 tools" reads as a working door with nothing behind it;
  // null lets the caller say "not reachable".
  it("distinguishes an unreadable manifest from an empty one", () => {
    expect(toolCount(null)).toBeNull();
    expect(toolCount({})).toBeNull();
    expect(toolCount({ tools: [] })).toBe(0);
    expect(resourceCount(null)).toBeNull();
    expect(resourceCount({ resources: [] })).toBe(0);
  });
});
