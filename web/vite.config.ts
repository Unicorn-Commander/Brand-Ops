import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// vite.config runs under Node at build time; declare the one global we read so
// tsc does not require @types/node just for this file.
declare const process: { env: Record<string, string | undefined> };

// The canonical public origin baked into index.html's absolute Open Graph /
// Twitter URLs (link-preview crawlers require an absolute og:image and og:url).
// Defaults to the prod host; a per-node build can override it, e.g.
//   BRAND_OPS_SITE_URL=https://brand-ops.magicunicorn.dev npm run build
const SITE_URL =
  process.env.BRAND_OPS_SITE_URL || "https://brand-ops.unicorncommander.ai";

// In dev, the Brand-Ops REST API runs on localhost:8000. The API client uses
// relative /api paths, so we proxy /api here and never bake in an absolute host.
export default defineConfig({
  plugins: [
    react(),
    {
      // Substitute the __SITE_URL__ placeholder in index.html with the absolute
      // origin so social/iMessage/RCS crawlers resolve og:image and og:url.
      name: "brand-ops-site-url",
      transformIndexHtml(html) {
        return html.split("__SITE_URL__").join(SITE_URL);
      },
    },
  ],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
