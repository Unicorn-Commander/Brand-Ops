import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import { api } from "../api/client";

/**
 * The signed-in user: an avatar chip in the topbar that opens identity,
 * Settings, and Sign out. Identity comes from GET /api/me (the verified
 * principal). Sign out clears the oauth2-proxy session and then ends the
 * Keycloak SSO session (logout_url is built server-side from the configured
 * realm), landing back on the app, which bounces to the login screen.
 *
 * In local dev (AUTH_REQUIRED off) the principal is anonymous: the chip says
 * so and Sign out is hidden, because there is no session to end.
 */
export function UserMenu() {
  const { data: who } = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => api.me(signal) });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!who) return null;

  const label = who.anonymous
    ? "local"
    : who.name || who.email || who.uc_uid;
  const initial = (who.name || who.email || "?").charAt(0).toUpperCase();

  const signOutHref = who.logout_url
    ? `/oauth2/sign_out?rd=${encodeURIComponent(
        `${who.logout_url}&post_logout_redirect_uri=${encodeURIComponent(
          window.location.origin + "/",
        )}`,
      )}`
    : null;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={who.anonymous ? "Local development session" : label}
        style={{
          width: 30,
          height: 30,
          borderRadius: 999,
          border: "1px solid var(--c-hairline)",
          background: who.anonymous
            ? "rgba(255,255,255,0.06)"
            : "radial-gradient(circle at 30% 28%, #d8b4fe, #7c3aed 58%, #2e1065)",
          color: "#fff",
          fontWeight: 700,
          fontSize: 13,
          cursor: "pointer",
          display: "grid",
          placeItems: "center",
        }}
      >
        {who.anonymous ? "·" : initial}
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 8px)",
            width: 252,
            zIndex: 80,
            borderRadius: 12,
            border: "1px solid var(--c-hairline)",
            background: "var(--c-surface)",
            boxShadow: "var(--shadow-paper)",
            overflow: "hidden",
          }}
        >
          <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--c-hairline-soft)" }}>
            <div style={{ fontWeight: 650, fontSize: 13.5, fontFamily: "var(--c-font-display)" }}>
              {who.anonymous ? "Anonymous · local dev" : label}
            </div>
            {!who.anonymous && who.email && who.email !== label && (
              <div style={{ fontSize: 11.5, color: "var(--c-ink-3)", marginTop: 2 }}>{who.email}</div>
            )}
            <div style={{ fontSize: 10.5, color: "var(--c-ink-3)", marginTop: 5 }}>
              workspace <b style={{ color: "var(--c-ink-2)" }}>{who.org}</b>
              {who.issuer ? ` · via ${who.issuer}` : ""}
            </div>
          </div>
          <NavLink
            to="/settings"
            onClick={() => setOpen(false)}
            style={{
              display: "block",
              padding: "10px 14px",
              fontSize: 13,
              color: "var(--c-ink)",
              textDecoration: "none",
            }}
          >
            Settings
          </NavLink>
          <a
            href="/design/"
            style={{
              display: "block",
              padding: "10px 14px",
              fontSize: 13,
              color: "var(--c-ink-2)",
              textDecoration: "none",
              borderBottom: "1px solid var(--c-hairline-soft)",
            }}
          >
            Design preview
          </a>
          {signOutHref ? (
            <a
              href={signOutHref}
              style={{
                display: "block",
                padding: "10px 14px",
                fontSize: 13,
                color: "#f49b8a",
                textDecoration: "none",
              }}
            >
              Sign out
            </a>
          ) : (
            <div style={{ padding: "10px 14px", fontSize: 11.5, color: "var(--c-ink-3)" }}>
              No session to end (auth is off locally)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
