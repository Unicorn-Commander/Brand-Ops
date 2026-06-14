/**
 * The app's motion system: a small set of NAMED spring presets and reusable
 * variants, so every screen animates with the same physical vocabulary instead
 * of one-off transitions. Import these rather than hand-tuning stiffness/damping
 * per component. Framer Motion reads the OS reduced-motion setting via
 * useReducedMotion(); index.css also clamps long animations there.
 */
import type { Transition, Variants } from "framer-motion";

/** Spring presets, tuned per use. Heavier damping = calmer, less bounce. */
export const spring = {
  /** Panels, drawers, sheets — substantial surfaces. */
  panel: { type: "spring", stiffness: 320, damping: 34 } as Transition,
  /** Cards, list items — the workhorse. */
  card: { type: "spring", stiffness: 260, damping: 26 } as Transition,
  /** Toggles, pills, small snappy moves. */
  snap: { type: "spring", stiffness: 460, damping: 32 } as Transition,
} as const;

/** The "expensive" easing curve for opacity/position fades (easeOutExpo-ish). */
export const easeExpo: Transition = {
  duration: 0.28,
  ease: [0.22, 1, 0.36, 1],
};

/**
 * Fast ease-out for FREQUENT, low-novelty interactions (hover, press, toggles,
 * disclosures). Kept under ~150ms so it reads as immediate rather than sluggish
 * (the premium-craft rule: don't over-animate things people trigger constantly).
 */
export const easeOutFast: Transition = {
  duration: 0.14,
  ease: [0.16, 1, 0.3, 1],
};

/** Route-level page transition. Pair with <AnimatePresence mode="wait">. */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: easeExpo },
  exit: { opacity: 0, y: -8, transition: { duration: 0.18 } },
};

/** Stagger container: children declare `item`, parent orchestrates timing. */
export const staggerContainer: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.04 } },
};

/** A staggered child: fades + rises + de-blurs in. The blur is the premium tell. */
export const staggerItem: Variants = {
  hidden: { opacity: 0, y: 16, filter: "blur(6px)" },
  show: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: spring.card,
  },
};
