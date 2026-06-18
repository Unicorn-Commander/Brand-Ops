import { useEffect, useState, type ReactNode } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import {
  X,
  ImageIcon,
  FileText,
  Layers,
  Link2,
  ShieldCheck,
  AlertTriangle,
  CheckCircle2,
  MessageSquareWarning,
  Archive,
  Undo2,
  ClipboardCheck,
} from "lucide-react";
import { Button } from "../../../components/Button";
import { useLightbox } from "../../../components/ImageLightbox";
import type {
  AssetCompliance,
  AssetLifecycleStatus,
  AssetRecord,
  ComplianceStatus,
} from "../../../lib/types";
import type { AssetGroup } from "./catalog";
import { spring, easeExpo } from "../../../lib/motion";
import { assetMorphId } from "./AssetGrid";
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
  /**
   * Move the shown record to a new lifecycle state (approve, request changes,
   * archive, re-flag). The parent runs api.transitionAsset and refreshes the
   * catalog and the activity feed. `note` carries the optional operator line
   * (used for "changes_requested"). Omitted when review is not wired here.
   */
  onTransition?: (
    record: AssetRecord,
    to: AssetLifecycleStatus,
    note?: string,
  ) => void;
  /** True while a transition for the shown record is in flight. */
  transitioning?: boolean;
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
    <section className="border-t border-white/8 px-5 py-4">
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
          className="flex items-center gap-2 rounded-xl border border-white/8 bg-chrome-raised/60 px-3 py-1.5"
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

/** Tailwind classes for a compliance status chip, by status. */
const STATUS_CHIP: Record<ComplianceStatus, string> = {
  pass: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
  warn: "border-amber-400/40 bg-amber-400/10 text-amber-300",
  fail: "border-red-500/40 bg-red-500/10 text-red-300",
  skipped: "border-white/12 bg-chrome-raised/60 text-text-muted",
};

/** Title-cased label for a compliance status. */
function statusLabel(status: ComplianceStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

/** Pill styling + label for each lifecycle state, on the suite palette. */
const LIFECYCLE_PILL: Record<
  AssetLifecycleStatus,
  { label: string; className: string }
> = {
  approved: {
    label: "Approved",
    className: "border-emerald-400/40 bg-emerald-400/10 text-emerald-300",
  },
  needs_review: {
    label: "Needs review",
    className: "border-amber-400/40 bg-amber-400/10 text-amber-300",
  },
  changes_requested: {
    label: "Changes requested",
    className: "border-red-500/40 bg-red-500/10 text-red-300",
  },
  archived: {
    label: "Archived",
    className: "border-white/12 bg-chrome-raised/60 text-text-muted",
  },
};

/** The current lifecycle state as a tinted pill. Null status renders nothing. */
function LifecyclePill({
  status,
}: {
  status: AssetLifecycleStatus | null | undefined;
}) {
  if (!status) return null;
  const pill = LIFECYCLE_PILL[status];
  if (!pill) return null;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${pill.className}`}
    >
      {pill.label}
    </span>
  );
}

/**
 * The compliance summary for the shown record: an overall status chip and score
 * plus the list of violations the check flagged. Rendered only when the record
 * carries a compliance result.
 */
function ComplianceSection({ compliance }: { compliance: AssetCompliance }) {
  const pct = Math.round(compliance.score * 100);
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${
            STATUS_CHIP[compliance.status]
          }`}
        >
          {statusLabel(compliance.status)}
        </span>
        <span className="text-sm text-text-muted">
          Score{" "}
          <span className="tabular-nums text-text">{pct}%</span>
        </span>
      </div>
      {compliance.violations.length > 0 ? (
        <ul className="space-y-1.5">
          {compliance.violations.map((v, i) => (
            <li
              key={`${i}-${v}`}
              className="flex items-start gap-2 rounded-xl border border-white/8 bg-chrome-raised/60 px-3 py-1.5"
            >
              <AlertTriangle
                size={14}
                aria-hidden
                className="mt-0.5 shrink-0 text-amber-300"
              />
              <span className="min-w-0 text-sm text-text">{v}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="flex items-center gap-2 text-sm text-text-muted">
          <CheckCircle2 size={14} aria-hidden className="text-emerald-400" />
          No violations found.
        </p>
      )}
    </div>
  );
}

/**
 * The review controls for the shown record, driven by its lifecycle state:
 *   - needs_review / changes_requested: Approve, Request changes (opens a note),
 *     and Archive.
 *   - approved: Archive, and a subtle re-flag (back to needs_review).
 *   - archived: a single un-archive back to needs_review.
 * Each control calls onTransition(record, to, note?). The whole cluster is
 * disabled while a transition is in flight. Records with no lifecycle state
 * (the gate never touched them) get a plain Approve to bring them into review.
 */
function ReviewSection({
  record,
  onTransition,
  transitioning,
}: {
  record: AssetRecord;
  onTransition: (
    record: AssetRecord,
    to: AssetLifecycleStatus,
    note?: string,
  ) => void;
  transitioning: boolean;
}) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  // True once we have fired a "request changes" submit and are waiting on it, so
  // the note panel keeps the "Send back" spinner until the transition resolves.
  const [submitting, setSubmitting] = useState(false);
  const status = record.lifecycle_status ?? null;

  // Reset the note draft whenever the shown record changes, so a note typed for
  // one asset never bleeds onto the next.
  useEffect(() => {
    setNoteOpen(false);
    setNote("");
    setSubmitting(false);
  }, [record.file]);

  // When an in-flight submit resolves (transitioning falls back to false),
  // collapse the note panel back to the button row. The record's file is
  // unchanged by a transition, so the file-keyed reset above will not fire. The
  // draft text is left intact (only the file-change reset clears it), so a
  // failed transition does not silently discard what the operator typed.
  useEffect(() => {
    if (submitting && !transitioning) {
      setNoteOpen(false);
      setSubmitting(false);
    }
  }, [submitting, transitioning]);

  const move = (to: AssetLifecycleStatus, withNote?: string) => {
    onTransition(record, to, withNote);
  };

  const submitChanges = () => {
    const trimmed = note.trim();
    setSubmitting(true);
    move("changes_requested", trimmed || undefined);
  };

  return (
    <div className="space-y-3">
      {/* The "Request changes" note. Optional: the operator can send with or
          without a line. Shown inline so the reason sits next to the action. */}
      {noteOpen && (
        <div className="space-y-2 rounded-xl border border-white/8 bg-chrome-raised/60 p-3">
          <label
            htmlFor="review-note"
            className="block text-xs font-medium text-text-muted"
          >
            What needs to change? (optional)
          </label>
          <textarea
            id="review-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            autoFocus
            placeholder="e.g. The mark sits too close to the edge; add clear space."
            className="w-full resize-y rounded-lg border border-white/8 bg-chrome-bg/60 px-3 py-2 text-sm text-text placeholder:text-text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setNoteOpen(false);
                setNote("");
              }}
              disabled={transitioning}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              icon={<MessageSquareWarning size={14} />}
              loading={transitioning}
              onClick={submitChanges}
            >
              Send back
            </Button>
          </div>
        </div>
      )}

      {!noteOpen && (
        <div className="flex flex-wrap gap-2">
          {status === "approved" ? (
            <>
              <Button
                variant="secondary"
                icon={<Archive size={15} />}
                loading={transitioning}
                onClick={() => move("archived")}
              >
                Archive
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<Undo2 size={14} />}
                disabled={transitioning}
                onClick={() => move("needs_review")}
              >
                Re-flag for review
              </Button>
            </>
          ) : status === "archived" ? (
            <Button
              variant="secondary"
              icon={<Undo2 size={15} />}
              loading={transitioning}
              onClick={() => move("needs_review")}
            >
              Restore to review
            </Button>
          ) : (
            <>
              <Button
                variant="primary"
                icon={<CheckCircle2 size={16} />}
                loading={transitioning}
                onClick={() => move("approved")}
              >
                Approve
              </Button>
              <Button
                variant="danger"
                icon={<MessageSquareWarning size={15} />}
                disabled={transitioning}
                onClick={() => setNoteOpen(true)}
              >
                Request changes
              </Button>
              <Button
                variant="ghost"
                size="sm"
                icon={<Archive size={14} />}
                disabled={transitioning}
                onClick={() => move("archived")}
              >
                Archive
              </Button>
            </>
          )}
        </div>
      )}
    </div>
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
              className={`flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left transition-colors ${
                active
                  ? "border-accent/60 bg-accent/10"
                  : "border-white/8 bg-chrome-raised/60 hover:bg-chrome-hover"
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
  onTransition,
  transitioning = false,
}: AssetDetailDrawerProps) {
  const open = group !== null;
  const reduce = useReducedMotion();
  const { open: openLightbox } = useLightbox();

  // Close on Escape while open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // The shown record: the selected version, else the current one. Computed only
  // when a group is open; AnimatePresence keeps the panel mounted through exit.
  const record = group
    ? group.versions.find((v) => v.file === activeFile) ?? group.current
    : null;
  const shownFile = record?.file ?? "";
  const createdAt = record ? formatDateTime(record.created_at) : null;
  // True when the hero shows the same render the grid card showed (the group's
  // current version) — only then does a grid card carry the matching layoutId
  // to morph from. Switching to an older version breaks that pairing.
  const isOpenedRender = record?.file === group?.current.file;
  // When the hero is morphing in, the panel must NOT animate a transform: a
  // shared-element (layoutId) child is measured in its ancestor's coordinate
  // space, and an animating x/scale on the panel distorts the morph (framer v12).
  // So while morphing we reveal the panel with opacity only and let the hero
  // carry the motion; otherwise we keep the right-side slide-over.
  const morphing = isOpenedRender && !reduce;

  return (
    <AnimatePresence>
      {group && record && (
        <div className="fixed inset-0 z-50">
          <motion.button
            type="button"
            aria-label="Close detail"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={easeExpo}
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
          />
          <motion.aside
            role="dialog"
            aria-modal="true"
            aria-label={`${assetTitle(record)} detail`}
            // Morphing: fade only (no transform) so the hero's layoutId projects
            // cleanly. Reduced motion: fade. Otherwise: the right-side slide-over.
            initial={morphing || reduce ? { opacity: 0 } : { x: "100%" }}
            animate={morphing || reduce ? { opacity: 1 } : { x: 0 }}
            exit={morphing || reduce ? { opacity: 0 } : { x: "100%" }}
            transition={morphing ? easeExpo : spring.panel}
            className="absolute right-0 top-0 flex h-full w-full max-w-md flex-col border-l border-white/8 bg-chrome-panel/95 shadow-float backdrop-blur-xl"
          >
            <header className="flex items-start justify-between gap-3 border-b border-white/8 px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate font-display text-base font-semibold tracking-tight text-text">
              {assetTitle(record)}
            </h2>
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <p className="truncate text-xs text-text-muted">{record.brand}</p>
              <LifecyclePill status={record.lifecycle_status} />
            </div>
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
          {/* Preview frame. Shows the rendered image when a URL is served,
              otherwise a labeled placeholder rather than a broken image. The
              hero of the OPENED render carries the same layoutId as its grid
              card, so framer morphs the thumbnail into this hero on open. An
              older version (switched via the history list) has no grid card to
              morph from, so it blur-ups in place instead. Reduced motion drops
              the morph and the older-version hero just cross-fades. */}
          <div className="px-5 pt-4">
            <div
              className="relative flex w-full flex-col items-center justify-center gap-2 overflow-hidden rounded-2xl border border-white/8 bg-chrome-raised text-text-faint"
              style={{ aspectRatio: aspectFor(record.size) }}
            >
              {record.url ? (
                morphing ? (
                  <motion.img
                    key={record.file}
                    layoutId={assetMorphId(record.file)}
                    src={record.url}
                    alt={`${assetTitle(record)} for ${record.brand}`}
                    onClick={() =>
                      record.url &&
                      openLightbox(
                        record.url,
                        `${assetTitle(record)} for ${record.brand}`,
                      )
                    }
                    className="h-full w-full cursor-zoom-in object-contain"
                  />
                ) : (
                  <motion.img
                    key={record.file}
                    initial={
                      reduce
                        ? { opacity: 0 }
                        : { opacity: 0, filter: "blur(12px)", scale: 0.96 }
                    }
                    animate={{ opacity: 1, filter: "blur(0px)", scale: 1 }}
                    transition={easeExpo}
                    src={record.url}
                    alt={`${assetTitle(record)} for ${record.brand}`}
                    onClick={() =>
                      record.url &&
                      openLightbox(
                        record.url,
                        `${assetTitle(record)} for ${record.brand}`,
                      )
                    }
                    className="h-full w-full cursor-zoom-in object-contain"
                  />
                )
              ) : (
                <>
                  <ImageIcon size={26} />
                  <span className="px-3 text-center text-xs leading-tight">
                    {shownFile}
                  </span>
                </>
              )}
            </div>
          </div>

          <Section icon={<FileText size={14} />} title="Prompt">
            {record.prompt ? (
              <p className="whitespace-pre-wrap rounded-xl border border-white/8 bg-chrome-raised/60 p-3 text-sm leading-relaxed text-text">
                {record.prompt}
              </p>
            ) : (
              <p className="text-sm text-text-muted">
                No prompt recorded for this render.
              </p>
            )}
          </Section>

          <Section icon={<Layers size={14} />} title="Details">
            <dl className="divide-y divide-white/5">
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

          {record.compliance && (
            <Section icon={<ShieldCheck size={14} />} title="Compliance">
              <ComplianceSection compliance={record.compliance} />
            </Section>
          )}

          {onTransition && (
            <Section icon={<ClipboardCheck size={14} />} title="Review">
              <ReviewSection
                record={record}
                onTransition={onTransition}
                transitioning={transitioning}
              />
            </Section>
          )}

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
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}

/** Aspect ratio for the preview frame; squares fall back when size is unknown. */
function aspectFor(size: number[]): string {
  if (size.length === 2 && size[0] > 0 && size[1] > 0) {
    return `${size[0]} / ${size[1]}`;
  }
  return "1 / 1";
}
