import type { ReactNode } from "react";

interface EmptyStateProps {
  /** Optional icon element (e.g. a lucide-react icon). */
  icon?: ReactNode;
  title: string;
  /** A short, plain explanation of why this is empty or what to do next. */
  description?: ReactNode;
  /** Optional action(s), e.g. a Button. */
  action?: ReactNode;
  className?: string;
}

/**
 * A centered placeholder for empty lists, no-results, and not-yet-built states.
 * Copy stays plain and direct. No marketing language.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`flex flex-col items-center justify-center gap-3 px-6 py-12 text-center ${className}`}
    >
      {icon && <div className="text-text-faint">{icon}</div>}
      <div className="space-y-1">
        <p className="text-sm font-medium text-text">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-text-muted">
            {description}
          </p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
