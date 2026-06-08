import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the Brand-Ops REST API runs on localhost:8000. The API client uses
// relative /api paths, so we proxy /api here and never bake in an absolute host.
export default defineConfig({
  plugins: [react()],
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
