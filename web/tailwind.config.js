/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Unicorn Commander dark chrome. Fixed app shell, independent of brand.
        chrome: {
          bg: "#0a0a0f",
          surface: "#101018",
          panel: "#14141d",
          raised: "#1b1b27",
          border: "#26263a",
          hover: "#1f1f2e",
        },
        text: {
          DEFAULT: "#f5f5fa",
          muted: "#9a9aae",
          faint: "#67677d",
        },
        // The active brand themes content. These resolve to CSS vars set at
        // runtime by applyBrandAccent(); see src/theme.ts. Defaults live in
        // index.css on :root so the UI renders before a brand is selected.
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          secondary: "rgb(var(--accent-secondary) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.02) inset, 0 8px 30px -12px rgba(0,0,0,0.6)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.4), 0 0 24px -6px rgb(var(--accent) / 0.45)",
      },
      borderRadius: {
        xl: "0.875rem",
      },
    },
  },
  plugins: [],
};
