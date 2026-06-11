import { useCallback, useId, useRef, useState } from "react";
import {
  UploadCloud,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  FileImage,
} from "lucide-react";
import { api, ApiError } from "../../../api/client";
import type { AssetRecord, AssetTypeKey } from "../../../lib/types";
import { Select, type SelectOption } from "./Select";

/**
 * The asset types a user can drop into. These mirror the six keys in
 * src/brand_ops/asset_types.py; labels are the human forms used elsewhere in the
 * GUI. Kept static so the dropzone needs no extra request to render.
 */
const ASSET_TYPE_OPTIONS: ReadonlyArray<{ value: AssetTypeKey; label: string }> = [
  { value: "logo", label: "Logo" },
  { value: "app_icon", label: "App icon" },
  { value: "og_image", label: "OG share image" },
  { value: "podcast_cover", label: "Podcast cover" },
  { value: "social_card", label: "Social card" },
  { value: "avatar", label: "Avatar" },
];

/** Client-side mirror of the server's accepted image types, for fast feedback. */
const ACCEPTED_MIME = new Set([
  "image/png",
  "image/jpeg",
  "image/svg+xml",
  "image/webp",
]);
const ACCEPTED_EXT = [".png", ".jpg", ".jpeg", ".svg", ".webp"];
const ACCEPT_ATTR = ACCEPTED_MIME.size > 0 ? [...ACCEPTED_MIME, ...ACCEPTED_EXT].join(",") : "";
const MAX_BYTES = 25 * 1024 * 1024;

type ItemStatus = "uploading" | "done" | "error";

interface UploadItem {
  id: string;
  name: string;
  status: ItemStatus;
  /** Set when status is "error". */
  error?: string;
}

interface UploadDropzoneProps {
  /** The brand to upload into. Required: uploads always target one brand. */
  brand: string | null;
  /** Human label for the target brand, shown in the helper line. */
  brandLabel?: string;
  /** Called once after one or more uploads succeed, to refresh the catalog. */
  onUploaded: (records: AssetRecord[]) => void;
}

/** A file extension or MIME type the server will accept as an image. */
function isAcceptedFile(file: File): boolean {
  if (file.type && ACCEPTED_MIME.has(file.type)) return true;
  // Some files arrive with an empty or vendor MIME (notably SVG); fall back to
  // the extension, matching the server's own fallback.
  const lower = file.name.toLowerCase();
  return ACCEPTED_EXT.some((ext) => lower.endsWith(ext));
}

/**
 * Drag-and-drop (with a click-to-pick fallback) for adding existing image assets
 * to the active brand kit. Each dropped image is POSTed to /api/assets/upload
 * with the chosen asset type; per-file progress and errors show inline, and the
 * parent is told to refresh the catalog once any upload lands so new assets
 * appear in the grid below.
 */
export function UploadDropzone({ brand, brandLabel, onUploaded }: UploadDropzoneProps) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [assetType, setAssetType] = useState<AssetTypeKey>("logo");
  const [dragOver, setDragOver] = useState(false);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  // A monotonic counter for stable item ids without leaning on the file name.
  const seq = useRef(0);

  const disabled = !brand;

  const handleFiles = useCallback(
    async (files: File[]) => {
      if (!brand || files.length === 0) return;

      // Seed one tracker row per file up front so the user sees them queue.
      const queued = files.map((file) => {
        const id = `u${seq.current++}`;
        const accepted = isAcceptedFile(file);
        const tooBig = file.size > MAX_BYTES;
        const item: UploadItem = accepted && !tooBig
          ? { id, name: file.name, status: "uploading" }
          : {
              id,
              name: file.name,
              status: "error",
              error: tooBig
                ? "Larger than the 25 MB limit"
                : "Not a supported image (png, jpg, svg, webp)",
            };
        return { file, item, willUpload: accepted && !tooBig };
      });
      setItems((prev) => [...queued.map((q) => q.item), ...prev]);

      setBusy(true);
      const succeeded: AssetRecord[] = [];
      for (const { file, item, willUpload } of queued) {
        if (!willUpload) continue;
        try {
          const record = await api.upload(file, brand, assetType);
          succeeded.push(record);
          setItems((prev) =>
            prev.map((it) => (it.id === item.id ? { ...it, status: "done" } : it)),
          );
        } catch (err) {
          const message =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Upload failed";
          setItems((prev) =>
            prev.map((it) =>
              it.id === item.id ? { ...it, status: "error", error: message } : it,
            ),
          );
        }
      }
      setBusy(false);
      if (succeeded.length > 0) onUploaded(succeeded);
    },
    [brand, assetType, onUploaded],
  );

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragOver(false);
      if (disabled) return;
      const files = Array.from(e.dataTransfer.files);
      void handleFiles(files);
    },
    [disabled, handleFiles],
  );

  const onPick = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files ? Array.from(e.target.files) : [];
      // Reset the input so picking the same file twice re-fires the change.
      e.target.value = "";
      void handleFiles(files);
    },
    [handleFiles],
  );

  function openPicker() {
    if (!disabled) inputRef.current?.click();
  }

  function clearFinished() {
    setItems((prev) => prev.filter((it) => it.status === "uploading"));
  }

  const typeOptions: SelectOption[] = ASSET_TYPE_OPTIONS.map((o) => ({
    value: o.value,
    label: o.label,
  }));
  const hasFinished = items.some((it) => it.status !== "uploading");

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <Select
          label="Upload as"
          allLabel="Choose a type"
          value={assetType}
          options={typeOptions}
          onChange={(v) => v && setAssetType(v as AssetTypeKey)}
          disabled={disabled}
        />
        {brandLabel && (
          <p className="pb-2 text-xs text-text-muted">
            Into <span className="font-medium text-text">{brandLabel}</span>
          </p>
        )}
      </div>

      <div
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-disabled={disabled}
        aria-label="Upload images by dropping them here or clicking to choose files"
        onClick={openPicker}
        onKeyDown={(e) => {
          if (disabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPicker();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        className={[
          "flex flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
          disabled
            ? "cursor-not-allowed border-white/8 bg-chrome-raised/30 opacity-60"
            : dragOver
              ? "cursor-pointer border-accent bg-accent/10 ring-2 ring-accent/40 shadow-glow"
              : "glass cursor-pointer border-white/10 hover:border-accent/60 hover:bg-chrome-hover",
        ].join(" ")}
      >
        <UploadCloud
          size={28}
          aria-hidden
          className={dragOver ? "text-accent" : "text-text-muted"}
        />
        <p className="text-sm font-medium text-text">
          {disabled
            ? "Select a brand to upload assets"
            : dragOver
              ? "Drop to upload"
              : "Drag images here, or click to choose"}
        </p>
        <p className="text-xs text-text-muted">
          PNG, JPG, SVG, or WebP up to 25 MB
        </p>
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="sr-only"
          onChange={onPick}
          disabled={disabled}
        />
      </div>

      {items.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-text-muted">
              {busy ? "Uploading…" : "Recent uploads"}
            </p>
            {hasFinished && !busy && (
              <button
                type="button"
                onClick={clearFinished}
                className="text-xs text-text-muted transition-colors hover:text-text"
              >
                Clear
              </button>
            )}
          </div>
          <ul className="space-y-1.5">
            {items.map((it) => (
              <li
                key={it.id}
                className="flex items-center gap-2 rounded-xl border border-white/8 bg-chrome-raised/60 px-3 py-2 text-sm"
              >
                <StatusIcon status={it.status} />
                <span className="min-w-0 flex-1 truncate text-text" title={it.name}>
                  {it.name}
                </span>
                {it.status === "error" && it.error && (
                  <span className="shrink-0 text-xs text-red-300" title={it.error}>
                    {it.error}
                  </span>
                )}
                {it.status === "done" && (
                  <span className="shrink-0 text-xs text-text-muted">Added</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: ItemStatus }) {
  if (status === "uploading") {
    return <Loader2 size={15} aria-hidden className="shrink-0 animate-spin text-accent" />;
  }
  if (status === "done") {
    return <CheckCircle2 size={15} aria-hidden className="shrink-0 text-accent" />;
  }
  if (status === "error") {
    return <AlertTriangle size={15} aria-hidden className="shrink-0 text-red-400" />;
  }
  return <FileImage size={15} aria-hidden className="shrink-0 text-text-muted" />;
}
