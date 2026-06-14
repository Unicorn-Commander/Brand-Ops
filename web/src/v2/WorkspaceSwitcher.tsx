import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, getActiveWorkspaceId, setActiveWorkspaceId } from "../api/client";

/**
 * The workspace switcher in the chrome topbar. Fed by GET /api/me/workspaces
 * (the caller's memberships). Selecting a workspace sets the X-Workspace-Id the
 * API client sends and refetches every scoped query, so the whole GUI re-scopes
 * to that tenant.
 *
 * With one workspace it is a plain label (no menu); with none -- the anonymous
 * local/dev session, or an authed caller with no membership -- it renders
 * nothing, so the chrome stays clean.
 */
export function WorkspaceSwitcher() {
  const { data } = useQuery({
    queryKey: ["me", "workspaces"],
    queryFn: ({ signal }) => api.meWorkspaces(signal),
    retry: 1,
  });
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const workspaces = data?.workspaces ?? [];
  const activeId = data?.active_workspace_id ?? null;

  // Reconcile a stale persisted pick: if it is not one of the caller's
  // workspaces, drop it. The backend already falls back to the default for a
  // stale header, but clearing it keeps the client honest.
  useEffect(() => {
    if (!data) return;
    const persisted = getActiveWorkspaceId();
    if (persisted && !workspaces.some((w) => w.workspace_id === persisted)) {
      setActiveWorkspaceId(null);
    }
  }, [data, workspaces]);

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

  // Nothing to show: no session, or an unscoped / no-membership caller.
  if (workspaces.length === 0) return null;

  const active =
    workspaces.find((w) => w.workspace_id === activeId) ?? workspaces[0];

  function choose(id: string) {
    setOpen(false);
    if (id === active.workspace_id) return;
    setActiveWorkspaceId(id);
    // Re-scope the whole GUI: every brand/asset query refetches with the new
    // header, and we return to the portfolio of the newly active workspace.
    queryClient.invalidateQueries();
    navigate("/");
  }

  // A single workspace: a plain chip, no menu.
  if (workspaces.length === 1) {
    return (
      <span className="workspace-chip">
        <span className="sw" /> {labelFor(active.slug)}
      </span>
    );
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        className="workspace-chip"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Switch workspace"
        style={{ cursor: "pointer", border: "none", font: "inherit" }}
      >
        <span className="sw" /> {labelFor(active.slug)}{" "}
        <span style={{ opacity: 0.5 }}>▾</span>
      </button>
      {open && (
        <div
          role="menu"
          style={{
            position: "absolute",
            right: 0,
            top: "calc(100% + 8px)",
            minWidth: 220,
            zIndex: 80,
            borderRadius: 12,
            border: "1px solid var(--c-hairline)",
            background: "var(--c-surface)",
            boxShadow: "var(--shadow-paper)",
            overflow: "hidden",
          }}
        >
          {workspaces.map((w) => {
            const on = w.workspace_id === active.workspace_id;
            return (
              <button
                key={w.workspace_id}
                type="button"
                role="menuitemradio"
                aria-checked={on}
                onClick={() => choose(w.workspace_id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  padding: "10px 14px",
                  fontSize: 13,
                  textAlign: "left",
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "var(--c-ink)",
                  fontWeight: on ? 650 : 400,
                }}
              >
                <span className="sw" />
                <span style={{ flex: 1 }}>{labelFor(w.slug)}</span>
                <span style={{ fontSize: 10.5, color: "var(--c-ink-3)" }}>{w.role}</span>
                {on && <span style={{ color: "var(--c-accent, #4cc9f0)" }}>✓</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function labelFor(slug: string): string {
  // Title-case the slug for the chip: "magic-unicorn" -> "Magic Unicorn".
  return slug
    .split("-")
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}
