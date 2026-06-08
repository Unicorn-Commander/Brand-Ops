import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Show a spinner and disable the button. */
  loading?: boolean;
  /** Optional leading icon (e.g. a lucide-react icon element). */
  icon?: ReactNode;
  children?: ReactNode;
}

const BASE =
  "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 " +
  "disabled:cursor-not-allowed disabled:opacity-50 select-none whitespace-nowrap";

const SIZES: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
};

const VARIANTS: Record<Variant, string> = {
  // Primary uses the brand accent as a solid fill against dark chrome text.
  primary:
    "bg-accent text-chrome-bg hover:bg-accent/90 active:bg-accent/80 shadow-glow",
  secondary:
    "bg-chrome-raised text-text border border-chrome-border hover:bg-chrome-hover",
  ghost: "bg-transparent text-text-muted hover:text-text hover:bg-chrome-hover",
  danger:
    "bg-red-500/15 text-red-300 border border-red-500/30 hover:bg-red-500/25",
};

/** The shared button. Accent-aware, with a built-in loading state. */
export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      className={`${BASE} ${SIZES[size]} ${VARIANTS[variant]} ${className}`}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? (
        <Spinner size={size === "sm" ? 14 : 16} />
      ) : (
        icon && <span className="shrink-0">{icon}</span>
      )}
      {children}
    </button>
  );
}
