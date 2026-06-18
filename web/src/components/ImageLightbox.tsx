import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";
import { easeExpo } from "../lib/motion";

/** The image the lightbox is showing, or null when it is closed. */
interface LightboxImage {
  src: string;
  alt: string;
}

interface LightboxContextValue {
  /**
   * Show an image full-size in the overlay. `alt` defaults to "" so a decorative
   * image stays decorative; pass a name for marks/photos. Calling open with a
   * falsy src is a no-op (nothing to show).
   */
  open: (src: string, alt?: string) => void;
}

const LightboxContext = createContext<LightboxContextValue | null>(null);

/**
 * Read the lightbox opener. Any component under <LightboxProvider> can call
 * open(src, alt) to show an image full-size, so we never prop-drill the overlay.
 */
export function useLightbox(): LightboxContextValue {
  const ctx = useContext(LightboxContext);
  if (!ctx) {
    throw new Error("useLightbox must be used within a LightboxProvider");
  }
  return ctx;
}

/**
 * Mounts the click-to-enlarge overlay once and hands every descendant an
 * open(src, alt) via useLightbox(). The overlay mirrors AssetDetailDrawer: an
 * AnimatePresence-gated fixed inset-0 z-50 layer with a motion backdrop, closed
 * on the backdrop, a close button, or Escape. The image renders object-contain
 * at 90vw x 90vh so it is shown whole, never cropped. When closed it renders
 * nothing (AnimatePresence carries the exit).
 */
export function LightboxProvider({ children }: { children: ReactNode }) {
  const [image, setImage] = useState<LightboxImage | null>(null);
  const reduce = useReducedMotion();

  const open = useCallback((src: string, alt?: string) => {
    if (!src) return;
    setImage({ src, alt: alt ?? "" });
  }, []);

  const close = useCallback(() => setImage(null), []);

  const isOpen = image !== null;

  // Close on Escape, and lock body scroll, only while open. Both clean up on
  // close so the page scrolls normally once the overlay is gone.
  useEffect(() => {
    if (!isOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [isOpen, close]);

  return (
    <LightboxContext.Provider value={{ open }}>
      {children}
      <AnimatePresence>
        {image && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label={image.alt || "Image"}
            className="fixed inset-0 z-50 grid place-items-center p-6"
          >
            <motion.button
              type="button"
              aria-label="Close"
              onClick={close}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={easeExpo}
              className="absolute inset-0 bg-black/70 backdrop-blur-sm"
            />
            <motion.img
              src={image.src}
              alt={image.alt}
              initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.96 }}
              transition={easeExpo}
              className="relative max-h-[90vh] max-w-[90vw] object-contain shadow-float"
            />
            <button
              type="button"
              onClick={close}
              aria-label="Close"
              className="absolute right-4 top-4 rounded-lg bg-black/40 p-2 text-white/90 backdrop-blur transition-colors hover:bg-black/60 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
            >
              <X size={20} />
            </button>
          </div>
        )}
      </AnimatePresence>
    </LightboxContext.Provider>
  );
}
