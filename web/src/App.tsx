import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import StudioPage from "./pages/studio/index";
import ShowcasePage from "./pages/showcase/index";
import LibraryPage from "./pages/library/index";
import TemplatesPage from "./pages/templates/index";
import BrandDetailPage from "./pages/brands/detail";
import BrandGuidelinesPage from "./pages/brands/guidelines";
import WorkflowsPage from "./pages/workflows/index";
import ActivityPage from "./pages/activity/index";

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
        <Route index element={<ShowcasePage />} />
        <Route path="brands" element={<ShowcasePage />} />
        <Route path="brands/:slug" element={<BrandDetailPage />} />
        <Route path="brands/:slug/guidelines" element={<BrandGuidelinesPage />} />
        <Route path="studio" element={<StudioPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="templates" element={<TemplatesPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        <Route path="activity" element={<ActivityPage />} />
        {/* Unknown paths fall back to the home showcase. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
