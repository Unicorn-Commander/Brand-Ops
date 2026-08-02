/**
 * The MCP door: where the agent-facing manifest actually lives, and how to read
 * it honestly.
 *
 * Two bugs lived here before this module existed.
 *
 * 1. The Crew page fetched `/.well-known/mcps.json` on the CONSOLE origin. In
 *    production nothing serves that path, so nginx's SPA fallback answers 200
 *    with index.html. `res.ok` is true, `res.json()` then throws on HTML, the
 *    query errors, and the MCP door renders "— tools / — resources" forever. A
 *    dead panel that looks like a real one. The manifest is served by the
 *    mcp.-prefixed sibling host (the Settings page already knew this; Crew did
 *    not), so the URL is derived once, here.
 *
 * 2. The Settings page printed "manifest · 21 tools" as a hardcoded string next
 *    to a live link. Nothing kept the 21 true. The count now comes from the
 *    manifest itself.
 *
 * The content-type check is the same guard `lib/download.ts` uses for asset
 * bytes: behind oauth2-proxy an expired session answers with an HTML login page,
 * and reporting a tool count parsed from a login page would be worse than
 * reporting nothing.
 */

export interface McpManifest {
  name?: string;
  version?: string;
  tools?: unknown[];
  resources?: unknown[];
}

/**
 * Where this deploy serves the MCP manifest.
 *
 * On a `brand-ops.<domain>` host the MCP surface is the `mcp.brand-ops.<domain>`
 * sibling. Anywhere else (local dev, a preview build) the same origin serves it.
 */
export function mcpManifestUrl(hostname: string): string {
  return hostname.startsWith("brand-ops.")
    ? `https://mcp.${hostname}/.well-known/mcps.json`
    : "/.well-known/mcps.json";
}

/**
 * Read the manifest, or null when this deploy does not answer with one.
 *
 * Returns null (never a partly-parsed object) for: a non-2xx, an HTML body (the
 * SPA fallback or a login page), or unparseable JSON — so a caller can say "not
 * reachable" instead of inventing a zero.
 */
export async function fetchMcpManifest(
  signal?: AbortSignal,
): Promise<McpManifest | null> {
  const url = mcpManifestUrl(window.location.hostname);
  let res: Response;
  try {
    res = await fetch(url, { signal, credentials: "omit" });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  if ((res.headers.get("content-type") ?? "").includes("text/html")) return null;
  try {
    return (await res.json()) as McpManifest;
  } catch {
    return null;
  }
}

/** How many tools the manifest declares, or null when it could not be read. */
export function toolCount(manifest: McpManifest | null | undefined): number | null {
  return Array.isArray(manifest?.tools) ? manifest.tools.length : null;
}

/** How many resources the manifest declares, or null when it could not be read. */
export function resourceCount(
  manifest: McpManifest | null | undefined,
): number | null {
  return Array.isArray(manifest?.resources) ? manifest.resources.length : null;
}
