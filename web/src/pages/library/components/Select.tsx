import { useId } from "react";
import { ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  label: string;
  /** Empty string means the "all" sentinel (no filter). */
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  /** Label for the "no filter" choice, e.g. "All brands". */
  allLabel: string;
  /** Disable when there is nothing to choose from. */
  disabled?: boolean;
}

/**
 * A compact labeled select styled for the dark chrome. A native <select> keeps
 * keyboard and screen-reader behavior correct; the chevron is decorative. The
 * empty string value is the "all" sentinel so the caller stores null for it.
 */
export function Select({
  label,
  value,
  options,
  onChange,
  allLabel,
  disabled = false,
}: SelectProps) {
  const id = useId();
  const isEmpty = options.length === 0;
  return (
    <label htmlFor={id} className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-text-muted">{label}</span>
      <div className="relative">
        <select
          id={id}
          value={value}
          disabled={disabled || isEmpty}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-full appearance-none rounded-lg border border-chrome-border bg-chrome-raised pl-3 pr-9 text-sm text-text outline-none transition-colors hover:bg-chrome-hover focus-visible:ring-2 focus-visible:ring-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <option value="">{allLabel}</option>
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown
          size={15}
          aria-hidden
          className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted"
        />
      </div>
    </label>
  );
}
