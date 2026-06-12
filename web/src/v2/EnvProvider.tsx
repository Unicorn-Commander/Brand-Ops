import { useEffect, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { getBrand } from "../api/client";
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

function applyBrandEnv(brand: BrandDetail): void {
  const root = document.documentElement;
  const colors = brand.visual.colors;
  const primary = colors.primary || "#9b8cff";
  const accentColor = colors.accent || primary;
  const bg = colors.background || "#07070b";
  const ink = colors.foreground || "#f2f1ee";
  root.style.setProperty("--b-bg", bg);
  root.style.setProperty("--b-bg-glow1", alpha(primary, 0.34));
  root.style.setProperty("--b-bg-glow2", alpha(accentColor, 0.18));
  root.style.setProperty("--b-ink", ink);
  root.style.setProperty("--b-ink-2", alpha(ink, 0.66));
  root.style.setProperty("--b-ink-3", alpha(ink, 0.44));
  root.style.setProperty("--b-primary", primary);
  root.style.setProperty("--b-accent", accentColor);
  root.style.setProperty("--b-hairline", alpha(primary, 0.14));
  root.style.setProperty("--b-font-display", fontStack(brand.visual.typography.display_font));
  root.style.setProperty("--b-font-body", fontStack(brand.visual.typography.primary_font));
}

function applyChromeEnv(): void {
  const root = document.documentElement;
  root.style.setProperty("--b-bg", "#07070b");
  root.style.setProperty("--b-bg-glow1", "rgba(155, 140, 255, 0.24)");
  root.style.setProperty("--b-bg-glow2", "rgba(76, 201, 240, 0.14)");
  root.style.setProperty("--b-ink", "#f2f1ee");
  root.style.setProperty("--b-ink-2", "rgba(242, 241, 238, 0.66)");
  root.style.setProperty("--b-ink-3", "rgba(242, 241, 238, 0.44)");
  root.style.setProperty("--b-primary", "#9b8cff");
  root.style.setProperty("--b-accent", "#4cc9f0");
  root.style.setProperty("--b-hairline", "rgba(155, 140, 255, 0.14)");
  root.style.setProperty("--b-font-display", FONT_STACKS.Inter);
  root.style.setProperty("--b-font-body", FONT_STACKS.Inter);
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
