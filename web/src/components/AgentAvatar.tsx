import { Sparkles } from "lucide-react";
import { useLightbox } from "./ImageLightbox";

interface AgentAvatarProps {
  /** The agent's name. Drives the fallback initial and the alt text. */
  name: string;
  /** A mark/photo URL. When set, it renders as the round image; else a gradient orb. */
  src?: string | null;
  /** Pixel diameter. Defaults to 40. */
  size?: number;
  /** Draw the accent gradient ring around the avatar. Defaults to true. */
  ring?: boolean;
}

/** First grapheme of the name, upper-cased, for the gradient-orb fallback. */
function initialOf(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed[0].toUpperCase() : "?";
}

/**
 * A round agent avatar. With a `src` (e.g. a brand mark) it renders that image
 * round behind a soft accent ring; without one it renders a gradient orb (the
 * accent to accent-secondary sweep) carrying the name's initial and a small
 * Sparkles glyph, so an agent without a mark still reads as a presence rather
 * than a blank circle. Used for Iris (the Director) and per-brand Stewards.
 */
export function AgentAvatar({
  name,
  src,
  size = 40,
  ring = true,
}: AgentAvatarProps) {
  const { open } = useLightbox();
  // The Sparkles glyph scales with the avatar but stays a tasteful accent, not a
  // badge; the initial owns the orb.
  const glyph = Math.max(9, Math.round(size * 0.26));
  const initialSize = Math.max(12, Math.round(size * 0.42));

  const ringClass = ring ? "accent-ring" : "ring-1 ring-white/10";

  if (src) {
    // The chip keeps the round object-cover crop; clicking it opens the full
    // square mark in the lightbox. stopPropagation so it does not also trigger a
    // parent row/card click (this avatar sits inside clickable cards).
    const enlarge = (e: { stopPropagation: () => void }) => {
      e.stopPropagation();
      open(src, name);
    };
    return (
      <span
        role="button"
        tabIndex={0}
        aria-label={`Enlarge ${name}`}
        onClick={enlarge}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            enlarge(e);
          }
        }}
        className={`relative inline-grid shrink-0 cursor-pointer place-items-center overflow-hidden rounded-full bg-chrome-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 ${ringClass}`}
        style={{ width: size, height: size }}
      >
        <img
          src={src}
          alt={name}
          className="h-full w-full rounded-full object-cover"
        />
      </span>
    );
  }

  return (
    <span
      aria-label={name}
      role="img"
      className={`accent-gradient relative inline-grid shrink-0 place-items-center rounded-full text-chrome-bg shadow-glow ${ringClass}`}
      style={{ width: size, height: size }}
    >
      <span
        className="font-display font-semibold leading-none tracking-tight"
        style={{ fontSize: initialSize }}
      >
        {initialOf(name)}
      </span>
      <Sparkles
        size={glyph}
        className="absolute right-0 top-0 -translate-y-[15%] translate-x-[15%] text-text drop-shadow"
      />
    </span>
  );
}
