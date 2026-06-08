import { useEffect, type ReactNode } from "react";
import { X, ImageIcon, FileText, Layers, Link2 } from "lucide-react";
import type { AssetRecord } from "../../../lib/types";
import type { AssetGroup } from "./catalog";
import {
  assetTitle,
  formatCost,
  formatDateTime,
  formatSize,
} from "./format";

interface AssetDetailDrawerProps {
  group: AssetGroup | null;
  /** Which version within the group is shown; defaults to the current one. */
  activeFile: string | null;
  onSelectVersion: (file: string) => void;
  onClose: () => void;
}

/** One label/value row in the metadata list. Omitted by the caller when empty. */
function MetaRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-xs uppercase tracking-wide text-text-faint">
        {label}
      </dt>
      <dd className="min-w-0 break-words text-right text-sm text-text">
        {value}
      </dd>
    </div>
  );
}

/** A bordered section with an icon heading. */
function Section({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="border-t border-chrome-border px-5 py-4">
      <h3 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-text-muted">
        <span className="text-text-faint">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function ReferenceList({ refs }: { refs: string[] }) {
  if (refs.length === 0) {
    return <p className="text-sm text-text-muted">None.</p>;
  }
  return (
    <ul className="space-y-1.5">
      {refs.map((ref) => (
        <li
          key={ref}
          className="flex items-center gap-2 rounded-lg border border-chrome-border bg-chrome-raised px-3 py-1.5"
        >
          <Link2 size={14} className="shrink-0 text-text-faint" />
          <span className="min-w-0 truncate font-mono text-xs text-text" title={ref}>
            {ref}
          </span>
        </li>
      ))}
    </ul>
  );
}

function VersionHistory({
  versions,
  activeFile,
  onSelect,
}: {
  versions: AssetRecord[];
  activeFile: string;
  onSelect: (file: string) => void;
}) {
  return (
    <ol className="space-y-1.5">
      {versions.map((v, i) => {
        const active = v.file === activeFile;
        const when = formatDateTime(v.created_at);
        // Newest first: index 0 is the latest render.
        const tag = i === 0 ? "Latest" : `v${versions.length - i}`;
        return (
          <li key={v.file}>
            <button
              type="button"
              onClick={() => onSelect(v.file)}
              aria-current={active ? "true" : undefined}
              className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                active
                  ? "border-accent/60 bg-accent/10"
                  : "border-chrome-border bg-chrome-raised hover:bg-chrome-hover"
              }`}
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate font-mono text-xs text-text" title={v.file}>
                  {v.file}
                </span>
                {when && (
                  <span className="text-[11px] text-text-muted">{when}</span>
                )}
              </span>
              <span
                className={`shrink-0 text-[11px] font-medium ${
                  i === 0 ? "text-accent" : "text-text-faint"
                }`}
              >
                {tag}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * A right-side slide-over showing everything the catalog knows about one asset:
 * the prompt that made it, the backend and model, size, cost, references, and
 * the version history of every render that shares its identity. Closes on the
 * backdrop, the close button, or Escape.
 */
export function AssetDetailDrawer({
  group,
  activeFile,
  onSelectVersion,
  onClose,
}: AssetDetailDrawerProps) {
  const open = group !== null;

  // Close on Escape while open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!group) return null;

  // The shown record: the selected version, else the current one.
  const record =
    group.versions.find((v) => v.file === activeFile) ?? group.current;
  const shownFile = record.file;
  const createdAt = formatDateTime(record.created_at);

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="Close detail"
        onClick={onClose}
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${assetTitle(record)} detail`}
        className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-chrome-border bg-chrome-panel shadow-panel"
      >
        <header className="flex items-start justify-between gap-3 border-b border-chrome-border px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-text">
              {assetTitle(record)}
            </h2>
            <p className="truncate text-xs text-text-muted">{record.brand}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1 rounded-lg p-1 text-text-muted transition-colors hover:bg-chrome-hover hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
          >
            <X size={18} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {/* Preview frame. The API serves no bytes, so this is a labeled
              placeholder rather than a broken image. */}
          <div className="px-5 pt-4">
            <div
              className="flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-lg border border-chrome-border bg-chrome-raised py-10 text-text-faint"
              style={{ aspectRatio: aspectFor(record.size) }}
            >
              <ImageIcon size={26} />
              <span className="px-3 text-center text-xs leading-tight">
                {shownFile}
              </span>
            </div>
          </div>

          <Section icon={<FileText size={14} />} title="Prompt">
            {record.prompt ? (
              <p className="whitespace-pre-wrap rounded-lg border border-chrome-border bg-chrome-raised p-3 text-sm leading-relaxed text-text">
                {record.prompt}
              </p>
            ) : (
              <p className="text-sm text-text-muted">
                No prompt recorded for this render.
              </p>
            )}
          </Section>

          <Section icon={<Layers size={14} />} title="Details">
            <dl className="divide-y divide-chrome-border/60">
              <MetaRow label="Backend" value={record.backend ?? "Unknown"} />
              <MetaRow label="Model" value={record.model ?? "Unknown"} />
              <MetaRow label="Size" value={formatSize(record.size)} />
              <MetaRow label="Cost" value={formatCost(record.cost_cents)} />
              {record.mime && <MetaRow label="Format" value={record.mime} />}
              <MetaRow
                label="Transparent"
                value={
                  record.transparent === null
                    ? "Unknown"
                    : record.transparent
                      ? "Yes"
                      : "No"
                }
              />
              {createdAt && <MetaRow label="Created" value={createdAt} />}
              <MetaRow
                label="File"
                value={
                  <span className="font-mono text-xs">{shownFile}</span>
                }
              />
            </dl>
          </Section>

          <Section icon={<Link2 size={14} />} title="References">
            <ReferenceList refs={record.reference_images} />
          </Section>

          <Section
            icon={<Layers size={14} />}
            title={`Version history (${group.versions.length})`}
          >
            {group.versions.length > 1 ? (
              <VersionHistory
                versions={group.versions}
                activeFile={shownFile}
                onSelect={onSelectVersion}
              />
            ) : (
              <p className="text-sm text-text-muted">
                This is the only render of this asset.
              </p>
            )}
          </Section>
        </div>
      </aside>
    </div>
  );
}

/** Aspect ratio for the preview frame; squares fall back when size is unknown. */
function aspectFor(size: number[]): string {
  if (size.length === 2 && size[0] > 0 && size[1] > 0) {
    return `${size[0]} / ${size[1]}`;
  }
  return "1 / 1";
}
