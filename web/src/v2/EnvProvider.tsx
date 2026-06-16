import { useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { getBrand } from "../api/client";
import { animateColorVars } from "../theme";
import type { BrandDetail } from "../lib/types";

const BUNDLED_FONTS = new Set([
  "Inter",
  "Space Grotesk",
]);

const FONT_STACKS: Record<string, string> = {
  Inter: "InterVariable, Inter, system-ui, sans-serif",
  "Space Grotesk": '"Space Grotesk Variable", "Space Grotesk", InterVariable, system-ui, sans-serif',
  "Plus Jakarta Sans": '"Plus Jakarta Sans", InterVariable, system-ui, sans-serif',
  Fraunces: "Fraunces, Georgia, serif",
  "Instrument Sans": '"Instrument Sans", InterVariable, system-ui, sans-serif',
  Newsreader: "Newsreader, Georgia, serif",
  "JetBrains Mono": '"JetBrains Mono", ui-monospace, monospace',
};

function alpha(hex: string, value: number): string {
  const h = hex.replace("#", "");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) return `rgba(255, 255, 255, ${value})`;
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${value})`;
}

export function fontStack(name: string | null | undefined): string {
  if (!name) return FONT_STACKS.Inter;
  return FONT_STACKS[name] ?? `${JSON.stringify(name)}, ${FONT_STACKS.Inter}`;
}

function loadFont(name: string | null | undefined): void {
  void name;
}

// True once an env has been applied this session. The first application (initial
// page load) snaps the color vars so there is no recolor flash; later changes
// tween them. Mirrors theme.ts's hasAppliedOnce for the --accent system.
let envAppliedOnce = false;

// The brand-book env's resolved values: solid colors (hex), the glow fills
// (rgba), the text + background fills, and the fonts. Split this way because we
// TWEEN the visible color vars (primary/accent/glows) and SNAP everything else
// (tweening a background or text fill reads as muddy; fonts can't tween at all).
interface EnvVars {
  primary: string;
  accent: string;
  glow1: string;
  glow2: string;
  bg: string;
  ink: string;
  ink2: string;
  ink3: string;
  hairline: string;
  fontDisplay: string;
  fontBody: string;
}

/**
 * Write an env to the document: the accent/glow color vars TWEEN (reusing
 * theme.ts's animateColorVars so they recolor in lockstep with --accent), while
 * the background, text, hairline, and font vars SNAP instantly. The first
 * application of the session snaps everything (no recolor flash on load), and
 * reduced motion snaps throughout (handled inside the tween).
 */
function applyEnv(v: EnvVars): void {
  const root = document.documentElement;

  // Snap the non-tweened vars (text + background fills, hairline, fonts).
  root.style.setProperty("--b-bg", v.bg);
  root.style.setProperty("--b-ink", v.ink);
  root.style.setProperty("--b-ink-2", v.ink2);
  root.style.setProperty("--b-ink-3", v.ink3);
  root.style.setProperty("--b-hairline", v.hairline);
  root.style.setProperty("--b-font-display", v.fontDisplay);
  root.style.setProperty("--b-font-body", v.fontBody);

  // The visible color vars: tween on a real change, snap on first paint.
  const colorTargets = [
    { prop: "--b-primary", to: v.primary, format: "rgb-or-rgba" as const },
    { prop: "--b-accent", to: v.accent, format: "rgb-or-rgba" as const },
    { prop: "--b-bg-glow1", to: v.glow1, format: "rgb-or-rgba" as const },
    { prop: "--b-bg-glow2", to: v.glow2, format: "rgb-or-rgba" as const },
  ];
  if (envAppliedOnce) {
    // group "env" so this tween is independent of the --accent tween that the
    // brand switch fires; the two run concurrently without cancelling each other.
    animateColorVars(colorTargets, { group: "env" });
  } else {
    for (const t of colorTargets) root.style.setProperty(t.prop, t.to);
  }
  envAppliedOnce = true;
}

function applyBrandEnv(brand: BrandDetail): void {
  const colors = brand.visual.colors;
  const primary = colors.primary || "#9b8cff";
  const accentColor = colors.accent || primary;
  const bg = colors.background || "#07070b";
  const ink = colors.foreground || "#f2f1ee";
  applyEnv({
    primary,
    accent: accentColor,
    glow1: alpha(primary, 0.34),
    glow2: alpha(accentColor, 0.18),
    bg,
    ink,
    ink2: alpha(ink, 0.66),
    ink3: alpha(ink, 0.44),
    hairline: alpha(primary, 0.14),
    fontDisplay: fontStack(brand.visual.typography.display_font),
    fontBody: fontStack(brand.visual.typography.primary_font),
  });
}

function applyChromeEnv(): void {
  applyEnv({
    primary: "#9b8cff",
    accent: "#4cc9f0",
    glow1: "rgba(155, 140, 255, 0.24)",
    glow2: "rgba(76, 201, 240, 0.14)",
    bg: "#07070b",
    ink: "#f2f1ee",
    ink2: "rgba(242, 241, 238, 0.66)",
    ink3: "rgba(242, 241, 238, 0.44)",
    hairline: "rgba(155, 140, 255, 0.14)",
    fontDisplay: FONT_STACKS.Inter,
    fontBody: FONT_STACKS.Inter,
  });
}

export function useBrandEnv(slug: string | undefined) {
  return useQuery({
    queryKey: ["brand", slug],
    queryFn: ({ signal }) => getBrand(slug as string, signal),
    enabled: Boolean(slug),
  });
}

export function EnvProvider({
  slug,
  children,
}: {
  slug?: string;
  children: ReactNode;
}) {
  const query = useBrandEnv(slug);

  useEffect(() => {
    document.body.dataset.env = slug ? "brand" : "chrome";
    document.body.classList.add("grain");
    return () => {
      document.body.dataset.env = "chrome";
    };
  }, [slug]);

  useEffect(() => {
    const brand = query.data;
    if (!brand) {
      applyChromeEnv();
      return;
    }
    loadFont(brand.visual.typography.display_font);
    loadFont(brand.visual.typography.primary_font);
    applyBrandEnv(brand);
  }, [query.data]);

  return <>{children}</>;
}

export function fontNote(brand: BrandDetail): string | null {
  const fonts = [
    brand.visual.typography.display_font,
    brand.visual.typography.primary_font,
  ].filter(Boolean);
  const missing = fonts.filter((name) => !BUNDLED_FONTS.has(name));
  if (missing.length === 0) return null;
  return `Font not bundled: ${[...new Set(missing)].join(", ")}. Rendering fallback.`;
}
