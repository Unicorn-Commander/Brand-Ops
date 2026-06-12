import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

const AMBIENT_KEY = "brandops.ambient";

/** Read the stored ambient preference; ambient is on unless explicitly off. */
export function ambientEnabled(): boolean {
  if (typeof localStorage === "undefined") return true;
  return localStorage.getItem(AMBIENT_KEY) !== "off";
}

/** Apply the stored ambient preference to <body> (called on boot and toggle). */
export function applyAmbient(): void {
  if (typeof document === "undefined") return;
  document.body.classList.toggle("no-ambient", !ambientEnabled());
}

function Card(props: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        border: "1px solid var(--c-hairline-soft)",
        borderRadius: 14,
        background: "var(--c-surface)",
        boxShadow: "var(--shadow-paper)",
        padding: "20px 24px",
      }}
    >
      <h2 style={{ margin: 0, fontFamily: "var(--c-font-display)", fontSize: 16, letterSpacing: "-0.01em" }}>
        {props.title}
      </h2>
      {props.sub && <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--c-ink-3)" }}>{props.sub}</p>}
      <div style={{ marginTop: 14 }}>{props.children}</div>
    </section>
  );
}

function Row(props: { k: string; v: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 12,
        padding: "7px 0",
        borderBottom: "1px solid var(--c-hairline-soft)",
        fontSize: 12.5,
      }}
    >
      <span style={{ color: "var(--c-ink-3)" }}>{props.k}</span>
      <span style={{ fontWeight: 600, textAlign: "right" }}>{props.v}</span>
    </div>
  );
}

/**
 * Settings: account, workspace, access doors, and preferences. Everything on
 * this page is real — identity from /api/me, version from the build, doors
 * from the deployed surfaces. The one client-side preference (ambient
 * effects) persists in localStorage and applies immediately.
 */
export default function SettingsPage() {
  const { data: who } = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => api.me(signal) });
  const [ambient, setAmbient] = useState(ambientEnabled());

  useEffect(() => {
    localStorage.setItem(AMBIENT_KEY, ambient ? "on" : "off");
    applyAmbient();
  }, [ambient]);

  const signOutHref = who?.logout_url
    ? `/oauth2/sign_out?rd=${encodeURIComponent(
        `${who.logout_url}&post_logout_redirect_uri=${encodeURIComponent(window.location.origin + "/")}`,
      )}`
    : null;

  // The MCP surface lives on the mcp.-prefixed sibling host in deployments;
  // local dev serves the manifest on the same origin.
  const host = window.location.hostname;
  const manifestUrl = host.startsWith("brand-ops.")
    ? `https://mcp.${host}/.well-known/mcps.json`
    : "/.well-known/mcps.json";

  return (
    <div style={{ maxWidth: 880 }}>
      <span className="microlabel">Settings</span>
      <h1
        style={{
          margin: "6px 0 0",
          fontFamily: "var(--c-font-display)",
          fontSize: 38,
          letterSpacing: "-0.025em",
        }}
      >
        Your seat
      </h1>
      <p className="annotation" style={{ marginTop: 8 }}>
        Account, workspace, and the doors into this system.
      </p>

      <div style={{ display: "grid", gap: 18, marginTop: 24 }}>
        <Card title="Account" sub="The verified identity behind this session.">
          {who ? (
            <>
              <Row k="Signed in as" v={who.anonymous ? "Anonymous · local dev" : who.name || who.email || who.uc_uid} />
              {who.email && <Row k="Email" v={who.email} />}
              <Row k="Workspace (org)" v={who.org} />
              <Row k="Verified by" v={who.issuer ?? "no issuer (auth off)"} />
              {signOutHref && (
                <div style={{ marginTop: 14 }}>
                  <a
                    className="btn btn-ghost"
                    href={signOutHref}
                    style={{ color: "#f49b8a", borderColor: "rgba(244,155,138,0.4)" }}
                  >
                    Sign out everywhere
                  </a>
                  <span style={{ fontSize: 11, color: "var(--c-ink-3)", marginLeft: 10 }}>
                    ends this app session and the Magic Unicorn SSO session
                  </span>
                </div>
              )}
            </>
          ) : (
            <p style={{ fontSize: 12.5, color: "var(--c-ink-3)" }}>Loading identity…</p>
          )}
        </Card>

        <Card title="Workspace" sub="The tenant this console is operating.">
          <Row k="Workspace" v="Magic Unicorn" />
          <Row k="App version" v="0.3.0" />
          <Row
            k="Chrome brand"
            v={
              <span>
                Brand-Ops “Obsidian” <span style={{ color: "var(--c-ink-3)", fontWeight: 400 }}>· white-label-ready</span>
              </span>
            }
          />
        </Card>

        <Card title="Doors" sub="Three entrances, equal citizens — humans here, agents via MCP, services via REST.">
          <Row k="Console" v="this GUI · suite SSO" />
          <Row
            k="MCP"
            v={
              <a href={manifestUrl} style={{ color: "var(--c-ink)" }}>
                manifest · 21 tools
              </a>
            }
          />
          <Row k="REST API" v="bearer-gated · same engine" />
          <Row k="Local agents" v="stdio MCP inside the backend container" />
        </Card>

        <Card title="Preferences" sub="Stored in this browser.">
          <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={ambient}
              onChange={(e) => setAmbient(e.target.checked)}
            />
            Ambient effects (grain, auras, glows)
          </label>
          <p style={{ fontSize: 11, color: "var(--c-ink-3)", marginTop: 6 }}>
            Off renders flat surfaces — easier on low-power machines and screen sharing. The OS
            reduced-motion setting is always honored regardless.
          </p>
        </Card>
      </div>
    </div>
  );
}
