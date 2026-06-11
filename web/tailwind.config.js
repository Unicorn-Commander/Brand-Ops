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
        // Body text. Variable Inter, self-hosted (see index.css import).
        sans: [
          "InterVariable",
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
        // Display / headings. Space Grotesk gives the editorial, branded voice.
        display: [
          "Space Grotesk Variable",
          "Space Grotesk",
          "InterVariable",
          "system-ui",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      boxShadow: {
        panel:
          "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 30px -12px rgba(0,0,0,0.6)",
        // A deeper float for cards that lift on hover.
        float:
          "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 24px 60px -20px rgba(0,0,0,0.75)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.4), 0 0 24px -6px rgb(var(--accent) / 0.45)",
        // A stronger accent halo for hero / focused elements.
        "glow-lg":
          "0 0 0 1px rgb(var(--accent) / 0.5), 0 0 60px -12px rgb(var(--accent) / 0.55)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.25rem",
        "3xl": "1.75rem",
      },
      fontSize: {
        // A tighter, more editorial display scale.
        display: ["3.5rem", { lineHeight: "1.02", letterSpacing: "-0.03em" }],
        hero: ["2.5rem", { lineHeight: "1.05", letterSpacing: "-0.025em" }],
      },
      backgroundImage: {
        // A faint dot-grid for empty canvases / hero areas.
        "dot-grid":
          "radial-gradient(rgb(255 255 255 / 0.05) 1px, transparent 1px)",
      },
      backgroundSize: {
        "dot-grid": "22px 22px",
      },
      transitionTimingFunction: {
        // A confident spring-ish ease used across micro-interactions.
        spring: "cubic-bezier(0.22, 1, 0.36, 1)",
      },
      keyframes: {
        // Slow drift for the aurora mesh blobs.
        "aurora-1": {
          "0%, 100%": { transform: "translate3d(-8%, -6%, 0) scale(1)" },
          "50%": { transform: "translate3d(10%, 8%, 0) scale(1.25)" },
        },
        "aurora-2": {
          "0%, 100%": { transform: "translate3d(12%, 4%, 0) scale(1.1)" },
          "50%": { transform: "translate3d(-10%, -8%, 0) scale(0.9)" },
        },
        "aurora-3": {
          "0%, 100%": { transform: "translate3d(0%, 10%, 0) scale(0.95)" },
          "50%": { transform: "translate3d(-6%, -10%, 0) scale(1.2)" },
        },
        // A sweeping highlight used for the "developing" asset reveal + skeletons.
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "aurora-1": "aurora-1 22s ease-in-out infinite",
        "aurora-2": "aurora-2 28s ease-in-out infinite",
        "aurora-3": "aurora-3 34s ease-in-out infinite",
        shimmer: "shimmer 1.6s ease-in-out infinite",
        "fade-up": "fade-up 0.4s cubic-bezier(0.22, 1, 0.36, 1) both",
      },
    },
  },
  plugins: [],
};
