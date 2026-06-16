import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { App } from "./App";
import { BrandProvider } from "./brand-context";
import { ViewModeProvider } from "./view-mode";
import "./index.css";

// One QueryClient for the app. Modest defaults: data is fresh for 30s and we do
// not refetch on window focus, which suits a generation tool where reads are
// cheap but not constantly changing.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <BrandProvider>
          <ViewModeProvider>
            <App />
            <Toaster
              theme="dark"
              position="bottom-right"
              expand
              gap={10}
              toastOptions={{
                style: {
                  background: "var(--c-surface)",
                  border: "1px solid var(--c-hairline)",
                  color: "var(--c-ink)",
                  borderRadius: "var(--radius-sm)",
                  boxShadow: "var(--shadow-deep)",
                },
              }}
            />
          </ViewModeProvider>
        </BrandProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
