import { Suspense, lazy } from "react";
import { Routes, Route, Navigate, Outlet, useParams } from "react-router-dom";
import { BrandLayout, Layout } from "./components/Layout";
import { Spinner } from "./components/Spinner";

// Page modules are lazy-loaded so each route is its own chunk and the initial
// load only ships the chrome (Layout, command palette, providers) plus the first
// page. The Layout/BrandLayout shells, the ErrorBoundary, the context providers,
// and the CommandPalette stay EAGER (imported above / inside Layout) so the
// chrome paints immediately; only the heavy page bodies split out. All page
// modules use a default export, so no .then() unwrap is needed.
const StudioPage = lazy(() => import("./pages/studio/index"));
const LibraryPage = lazy(() => import("./pages/library/index"));
const BrandGuidelinesPage = lazy(() => import("./pages/brands/guidelines"));
const PortfolioPage = lazy(() => import("./pages/v2/PortfolioPage"));
const BrandHQPage = lazy(() => import("./pages/v2/BrandHQPage"));
const MarkPage = lazy(() => import("./pages/v2/MarkPage"));
const StandardsPage = lazy(() => import("./pages/v2/StandardsPage"));
const CrewPage = lazy(() => import("./pages/v2/CrewPage"));
const SettingsPage = lazy(() => import("./pages/v2/SettingsPage"));
const NewBrandPage = lazy(() => import("./pages/v2/NewBrandPage"));

/**
 * App routing. The Layout renders the persistent chrome (aurora + nav + brand
 * switcher + command palette) around a routed outlet.
 *
 * Brands-first: the home is the Showcase (the visual brand browse), with
 * /brands rendering the same screen, and /brands/:slug opening a single brand's
 * record. The Studio (the agent console) lives at /studio.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route element={<SuspenseOutlet />}>
          <Route index element={<PortfolioPage />} />
          <Route path="library" element={<LibraryPage />} />
          <Route path="standards" element={<StandardsPage />} />
          <Route path="crew" element={<CrewPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="brands/new" element={<NewBrandPage />} />
          <Route path="brands" element={<PortfolioPage />} />
          <Route path="brands/:slug" element={<LegacyBrandRedirect target="overview" />} />
          <Route path="brands/:slug/guidelines" element={<LegacyBrandRedirect target="book" />} />
          <Route path="studio" element={<StudioPage />} />
          <Route path="templates" element={<Navigate to="/b/magic-unicorn/studio" replace />} />
          <Route path="workflows" element={<Navigate to="/b/magic-unicorn/studio" replace />} />
          <Route path="activity" element={<Navigate to="/standards" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Route>
      <Route path="/b/:slug" element={<BrandLayout />}>
        <Route element={<SuspenseOutlet />}>
          <Route index element={<Navigate to="overview" replace />} />
          <Route path="overview" element={<BrandHQPage />} />
          <Route path="mark" element={<MarkPage />} />
          <Route path="book" element={<BrandGuidelinesPage />} />
          <Route path="assets" element={<LibraryPage />} />
          <Route path="studio" element={<StudioPage />} />
          <Route path="grade" element={<StandardsPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

/**
 * A layout route that wraps the routed page in a single Suspense boundary. It
 * sits BELOW the chrome Layout (which renders this via its Outlet), so while a
 * lazy page chunk loads the nav/topbar stays put and only the content region
 * shows the fallback — no full-screen flash on navigation.
 */
function SuspenseOutlet() {
  return (
    <Suspense fallback={<RouteFallback />}>
      <Outlet />
    </Suspense>
  );
}

/** Centered, brand-accented loading marker shown while a route chunk loads. */
function RouteFallback() {
  return (
    <div
      role="status"
      aria-label="Loading"
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "40vh",
      }}
    >
      <Spinner size={28} />
    </div>
  );
}

function LegacyBrandRedirect({ target }: { target: string }) {
  const { slug } = useParams();
  if (!slug) return <Navigate to="/" replace />;
  return <Navigate to={`/b/${slug}/${target}`} replace />;
}
