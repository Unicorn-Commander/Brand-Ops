import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { BrandLayout, Layout } from "./components/Layout";
import StudioPage from "./pages/studio/index";
import LibraryPage from "./pages/library/index";
import BrandGuidelinesPage from "./pages/brands/guidelines";
import PortfolioPage from "./pages/v2/PortfolioPage";
import BrandHQPage from "./pages/v2/BrandHQPage";
import MarkPage from "./pages/v2/MarkPage";
import StandardsPage from "./pages/v2/StandardsPage";
import CrewPage from "./pages/v2/CrewPage";
import SettingsPage from "./pages/v2/SettingsPage";
import NewBrandPage from "./pages/v2/NewBrandPage";

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
      <Route path="/b/:slug" element={<BrandLayout />}>
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<BrandHQPage />} />
        <Route path="mark" element={<MarkPage />} />
        <Route path="book" element={<BrandGuidelinesPage />} />
        <Route path="assets" element={<LibraryPage />} />
        <Route path="studio" element={<StudioPage />} />
        <Route path="grade" element={<StandardsPage />} />
      </Route>
    </Routes>
  );
}

function LegacyBrandRedirect({ target }: { target: string }) {
  const { slug } = useParams();
  if (!slug) return <Navigate to="/" replace />;
  return <Navigate to={`/b/${slug}/${target}`} replace />;
}
