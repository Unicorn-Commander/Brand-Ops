/**
 * Small presentational atoms shared by the workflow cards and result panels.
 *
 * These are intentionally tiny and unopinionated about data: forms compose
 * them, result panels compose them. Dark chrome plus the brand accent
 * throughout. No marketing copy.
 */
import type { ReactNode } from "react";
import { Info } from "lucide-react";

/** Shared input/select styling so every card form matches. */
export const FIELD_LABEL = "block text-xs font-medium text-text";
export const FIELD_HINT = "mt-1 text-xs text-text-faint";
export const INPUT_BASE =
  "mt-1.5 w-full rounded-lg border border-chrome-border bg-chrome-raised px-3 py-2 text-sm text-text " +
  "placeholder:text-text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60";

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: ReactNode;
  type?: "text" | "url";
}

/** A labeled single-line text input. */
export function TextField({
  label,
  value,
  onChange,
  placeholder,
  hint,
  type = "text",
}: TextFieldProps) {
  return (
    <div>
      <label className={FIELD_LABEL}>
        {label}
        <input
          type={type}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className={INPUT_BASE}
        />
      </label>
      {hint && <p className={FIELD_HINT}>{hint}</p>}
    </div>
  );
}

interface SelectFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  hint?: ReactNode;
}

/** A labeled select. */
export function SelectField({
  label,
  value,
  onChange,
  options,
  hint,
}: SelectFieldProps) {
  return (
    <div>
      <label className={FIELD_LABEL}>
        {label}
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={INPUT_BASE}
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>
      {hint && <p className={FIELD_HINT}>{hint}</p>}
    </div>
  );
}

/** A read-only labeled value box, e.g. the active brand. */
export function ReadonlyField({
  label,
  value,
  hint,
  muted,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  muted?: boolean;
}) {
  return (
    <div>
      <span className={FIELD_LABEL}>{label}</span>
      <div
        className={`${INPUT_BASE} flex items-center ${muted ? "text-text-faint" : "text-text"}`}
        aria-readonly="true"
      >
        {value}
      </div>
      {hint && <p className={FIELD_HINT}>{hint}</p>}
    </div>
  );
}

/** A monospace block for prompts and paths, with a label above. */
export function CodeBlock({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  return (
    <div>
      {label && <p className="mb-1 text-xs font-medium text-text-muted">{label}</p>}
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl border border-white/8 bg-chrome-bg/50 p-3 font-mono text-xs leading-relaxed text-text shadow-inner">
        {children}
      </pre>
    </div>
  );
}

/** A labeled bullet list (reasons, tweaks). Renders nothing when empty. */
export function BulletList({
  label,
  items,
  accent = false,
}: {
  label: string;
  items: string[];
  /** Use the brand accent for the markers (e.g. actionable tweaks). */
  accent?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-text-muted">{label}</p>
      <ul className="space-y-1.5">
        {items.map((item, i) => (
          <li
            key={`${i}-${item}`}
            className="flex items-start gap-2 text-sm text-text"
          >
            <span
              aria-hidden="true"
              className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                accent ? "bg-accent" : "bg-text-faint"
              }`}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

type NoticeTone = "info" | "warn" | "error";

const NOTICE_STYLES: Record<NoticeTone, { box: string; icon: string; title: string }> = {
  info: {
    box: "border-chrome-border bg-chrome-bg/40",
    icon: "text-text-muted",
    title: "text-text",
  },
  warn: {
    box: "border-amber-500/30 bg-amber-500/[0.07]",
    icon: "text-amber-300",
    title: "text-amber-200",
  },
  error: {
    box: "border-red-500/30 bg-red-500/[0.07]",
    icon: "text-red-300",
    title: "text-red-200",
  },
};

/** A labeled notice banner (e.g. "Needs the Brigade hub"). */
export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: NoticeTone;
  title: string;
  children?: ReactNode;
}) {
  const styles = NOTICE_STYLES[tone];
  return (
    <div className={`rounded-lg border p-3 ${styles.box}`}>
      <div className="flex items-start gap-2.5">
        <Info size={16} className={`mt-0.5 shrink-0 ${styles.icon}`} />
        <div className="min-w-0">
          <p className={`text-sm font-semibold ${styles.title}`}>{title}</p>
          {children && (
            <div className="mt-1 text-sm text-text-muted">{children}</div>
          )}
        </div>
      </div>
    </div>
  );
}

/** A compact key/value row for run inputs. */
export function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-text-faint">{label}</dt>
      <dd className="truncate font-mono text-xs text-text">{value}</dd>
    </div>
  );
}
