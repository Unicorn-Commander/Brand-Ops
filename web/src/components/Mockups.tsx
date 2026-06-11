/**
 * Hand-rolled mockup frames that composite a brand asset into a believable
 * real-world context — the single biggest perceived-craft lever for a brand
 * tool. Each takes an image URL (and a little brand context) and renders it in
 * a phone home screen, a browser tab, a social link-preview, or a business card.
 * No device-frame dependency (those ship stale bezels); ~30 lines each, exact
 * control. When no image is available they show a tasteful placeholder so the
 * frame still reads.
 */
import { ImageIcon } from "lucide-react";

function Placeholder({ rounded = "rounded-md" }: { rounded?: string }) {
  return (
    <div
      className={`grid h-full w-full place-items-center bg-chrome-raised text-text-faint ${rounded}`}
    >
      <ImageIcon size={18} />
    </div>
  );
}

/** App icon on an iOS-style home screen, over a brand-tinted wallpaper. */
export function PhoneHomePreview({
  icon,
  label = "Brand",
  wallpaper,
}: {
  icon?: string;
  label?: string;
  /** A CSS background for the wallpaper; defaults to the brand accent gradient. */
  wallpaper?: string;
}) {
  return (
    <div className="relative mx-auto h-[380px] w-[190px] rounded-[2.6rem] bg-black/80 p-2.5 shadow-float ring-1 ring-white/10">
      <div className="absolute left-1/2 top-3 z-10 h-5 w-20 -translate-x-1/2 rounded-full bg-black" />
      <div
        className="relative h-full w-full overflow-hidden rounded-[2rem]"
        style={{
          background:
            wallpaper ??
            "linear-gradient(160deg, rgb(var(--accent) / 0.85), rgb(var(--accent-secondary) / 0.7))",
        }}
      >
        <div className="absolute inset-x-0 top-7 text-center text-xs font-medium tabular-nums text-white/90">
          9:41
        </div>
        <div className="absolute inset-x-0 top-16 grid grid-cols-4 gap-x-3 gap-y-4 px-4">
          <div className="flex flex-col items-center gap-1">
            <div className="h-11 w-11 overflow-hidden rounded-[13px] shadow-md ring-1 ring-black/20">
              {icon ? (
                <img src={icon} alt={`${label} app icon`} className="h-full w-full object-cover" />
              ) : (
                <Placeholder rounded="rounded-[13px]" />
              )}
            </div>
            <span className="max-w-[48px] truncate text-[8px] text-white drop-shadow">
              {label}
            </span>
          </div>
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="h-11 w-11 rounded-[13px] bg-white/20 backdrop-blur-sm" />
          ))}
        </div>
      </div>
    </div>
  );
}

/** Favicon in a browser tab. */
export function TabPreview({
  favicon,
  title,
  domain,
}: {
  favicon?: string;
  title: string;
  domain?: string;
}) {
  return (
    <div className="flex w-72 items-end gap-1 rounded-t-xl bg-chrome-raised px-2 pt-2">
      <div className="flex max-w-[220px] items-center gap-2 rounded-t-lg bg-chrome-bg px-3 py-1.5">
        <span className="h-4 w-4 shrink-0 overflow-hidden rounded-[3px]">
          {favicon ? (
            <img src={favicon} alt="" className="h-full w-full object-cover" />
          ) : (
            <span className="block h-full w-full bg-accent" />
          )}
        </span>
        <span className="truncate text-xs text-text-muted" title={domain}>
          {title}
        </span>
        <span className="ml-1 text-text-faint">×</span>
      </div>
    </div>
  );
}

/** A social / link-preview unfurl. Image is the canonical OG 1.91:1. */
export function LinkPreviewCard({
  ogImage,
  domain,
  title,
  description,
}: {
  ogImage?: string;
  domain: string;
  title: string;
  description: string;
}) {
  return (
    <div className="w-[400px] max-w-full overflow-hidden rounded-xl border border-chrome-border bg-chrome-panel">
      <div className="aspect-[1.91/1] w-full bg-chrome-raised">
        {ogImage ? (
          <img src={ogImage} alt="" className="h-full w-full object-cover" />
        ) : (
          <Placeholder rounded="" />
        )}
      </div>
      <div className="space-y-1 p-3">
        <p className="text-[11px] uppercase tracking-wide text-text-faint">{domain}</p>
        <p className="line-clamp-1 text-sm font-semibold text-text">{title}</p>
        <p className="line-clamp-2 text-xs text-text-muted">{description}</p>
      </div>
    </div>
  );
}

/** A business card with the logo, tilting slightly toward the cursor on hover. */
export function BusinessCardPreview({
  logo,
  name,
  tagline,
}: {
  logo?: string;
  name: string;
  tagline?: string | null;
}) {
  return (
    <div className="group [perspective:1000px]">
      <div className="flex aspect-[1.75/1] w-72 flex-col justify-between rounded-2xl border border-white/10 bg-gradient-to-br from-chrome-raised to-chrome-panel p-5 shadow-float transition-transform duration-300 ease-spring [transform:rotateX(0deg)] group-hover:[transform:rotateX(6deg)_rotateY(-6deg)]">
        <div className="h-8">
          {logo ? (
            <img src={logo} alt={`${name} logo`} className="h-8 w-auto object-contain" />
          ) : (
            <span
              className="inline-block h-7 w-7 rounded-md"
              style={{
                background:
                  "linear-gradient(135deg, rgb(var(--accent)), rgb(var(--accent-secondary)))",
              }}
            />
          )}
        </div>
        <div>
          <p className="font-display text-sm font-semibold text-text">{name}</p>
          {tagline && <p className="text-xs text-text-muted">{tagline}</p>}
        </div>
      </div>
    </div>
  );
}
