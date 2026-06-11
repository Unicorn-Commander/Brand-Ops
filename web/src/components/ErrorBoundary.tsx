import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCw, Home } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * A route-level error boundary. Without one, any render error in a page
 * unmounts the whole SPA and leaves a blank screen (the symptom: navigate, or
 * hit the browser Back button, and the content vanishes). This catches the
 * throw, keeps the app chrome, and shows a recoverable panel with the real
 * message so the failure is visible instead of silent.
 *
 * Wrap it with a `key` that changes on navigation (the route path) so moving to
 * another route remounts the boundary and clears the error automatically.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface the stack in the console so a blank-page report is debuggable.
    console.error("[brand-ops] page error:", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex justify-center py-16">
        <div className="glass max-w-lg rounded-2xl p-7 text-center shadow-panel">
          <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-full bg-amber-400/10 text-amber-300">
            <AlertTriangle size={22} />
          </div>
          <h2 className="font-display text-lg font-semibold text-text">
            This page hit an error
          </h2>
          <p className="mx-auto mt-2 max-w-md text-sm text-text-muted">
            The rest of the app is fine. Reload to try again, or head back to your
            brands. If it keeps happening, the message below says what broke.
          </p>
          <pre className="mt-4 max-h-32 overflow-auto rounded-lg border border-white/10 bg-black/30 p-3 text-left text-xs text-text-faint">
            {error.message || String(error)}
          </pre>
          <div className="mt-5 flex items-center justify-center gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 rounded-xl bg-accent px-3 py-2 text-sm font-medium text-chrome-bg shadow-glow transition-colors hover:bg-accent/90"
            >
              <RotateCw size={15} /> Reload
            </button>
            <a
              href="/"
              className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-text hover:bg-white/[0.08]"
            >
              <Home size={15} /> Back to Brands
            </a>
          </div>
        </div>
      </div>
    );
  }
}
