import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { me, provisionWorkspace, setActiveWorkspaceId } from "../api/client";
import { staggerContainer, staggerItem } from "../lib/motion";

/**
 * The self-serve signup screen: a first-run surface for an authenticated suite
 * user who has no workspace yet.
 *
 * The rest of the app is deny-by-default, so without a workspace this caller
 * would meet an empty, dead-end app (the portfolio's front door leads to brand
 * creation, which needs a workspace). This claims that state first: name a
 * workspace, create it, and land in the real app. The backend mints an owner,
 * default workspace for the caller (POST /api/onboarding/provision); on success
 * we re-scope the GUI to it and invalidate every query so the gate above us flips
 * and the portfolio renders.
 *
 * Shown by the chrome Layout only when GET /api/onboarding reports
 * `needs_onboarding` (self-serve enabled AND no workspace). Copy honors the house
 * rule: no em-dashes, no marketing-speak. Motion is the shared blur-rise stagger.
 */
export default function SignupWelcome({ suggestedName }: { suggestedName?: string | null }) {
  const reduce = useReducedMotion();
  const queryClient = useQueryClient();
  const [name, setName] = useState(suggestedName ?? "");
  // A quiet sign-out affordance: a shared-realm SSO user who landed here by
  // mistake can leave without creating anything. The logout URL comes from /me.
  const meQ = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => me(signal), retry: 1 });

  const provision = useMutation({
    mutationFn: () => provisionWorkspace(name),
    onSuccess: (ws) => {
      // Scope the client to the new workspace explicitly, then refetch everything:
      // the onboarding gate re-resolves to needs_onboarding=false and the app
      // renders the (empty) portfolio for this fresh workspace.
      setActiveWorkspaceId(ws.workspace_id);
      queryClient.invalidateQueries();
      toast.success(ws.created ? "Workspace created" : "Workspace ready");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Could not create the workspace");
    },
  });

  const initial = reduce ? false : "hidden";
  const busy = provision.isPending;

  function submit() {
    if (busy) return;
    provision.mutate();
  }

  return (
    <main className="v2-wrap">
      <motion.div
        variants={staggerContainer}
        initial={initial}
        animate="show"
        style={{
          maxWidth: 600,
          margin: "0 auto",
          padding: "84px 0 80px",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          textAlign: "center",
        }}
      >
        <motion.span variants={staggerItem} className="microlabel" style={{ marginBottom: 18 }}>
          Welcome to Brand-Ops
        </motion.span>

        <motion.h1
          variants={staggerItem}
          style={{
            margin: 0,
            fontFamily: "var(--c-font-display)",
            fontWeight: 700,
            fontSize: 42,
            lineHeight: 1.05,
            letterSpacing: "-0.028em",
            color: "var(--c-ink)",
          }}
        >
          Set up your workspace.
        </motion.h1>

        <motion.p
          variants={staggerItem}
          style={{
            margin: "14px 0 0",
            fontSize: 16,
            lineHeight: 1.55,
            color: "var(--c-ink-2)",
            maxWidth: 460,
          }}
        >
          A workspace holds your brands, assets, and agents. Name it and you are in.
        </motion.p>

        <motion.form
          variants={staggerItem}
          className="omnibar"
          style={{ width: "100%", maxWidth: 480, marginTop: 30 }}
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <span className="orb">
            <Sparkles size={15} strokeWidth={2} />
          </span>
          <input
            autoFocus={!reduce}
            aria-label="Workspace name"
            placeholder="Your workspace name"
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
          <button className="btn btn-ink" type="submit" style={{ flex: "none" }} disabled={busy}>
            {busy ? "Creating..." : "Create workspace"}
            {!busy && <ArrowRight size={14} strokeWidth={2.2} />}
          </button>
        </motion.form>

        <motion.p
          variants={staggerItem}
          style={{ margin: "18px 0 0", fontSize: 12, color: "var(--c-ink-3)" }}
        >
          You can rename or add more later.
        </motion.p>

        {meQ.data?.logout_url && (
          <motion.a
            variants={staggerItem}
            href={meQ.data.logout_url}
            style={{
              marginTop: 28,
              fontSize: 12,
              color: "var(--c-ink-3)",
              textDecoration: "none",
            }}
          >
            Not {meQ.data.name ?? "you"}? Sign out
          </motion.a>
        )}
      </motion.div>
    </main>
  );
}
