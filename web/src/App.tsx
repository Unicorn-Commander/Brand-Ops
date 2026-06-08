import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import StudioPage from "./pages/studio/index";
import LibraryPage from "./pages/library/index";
import BrandsPage from "./pages/brands/index";
import WorkflowsPage from "./pages/workflows/index";

/**
 * App routing. The Layout renders the persistent chrome (nav + brand switcher)
 * around a routed outlet. Each section is a page module the screen teams own;
 * the scaffold ships stub bodies that compile and render today.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<StudioPage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="brands" element={<BrandsPage />} />
        <Route path="workflows" element={<WorkflowsPage />} />
        {/* Unknown paths fall back to Studio. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
