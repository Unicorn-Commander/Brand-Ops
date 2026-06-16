import { defineConfig } from "vitest/config";

// Standalone vitest config (kept separate from vite.config.ts, which carries the
// dev server + /api proxy that the test run does not need). jsdom is the test
// environment so the recents tests get a real localStorage and the theme tests
// can touch document where needed; node would not provide either.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
    globals: false,
  },
});
