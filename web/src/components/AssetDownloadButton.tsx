import { useState, type CSSProperties } from "react";
import { Download } from "lucide-react";
import { toast } from "sonner";
import { Spinner } from "./Spinner";
import { downloadAsset, downloadFile } from "../lib/download";
import type { AssetRecord } from "../lib/types";

/**
 * "Give me that file", available wherever a file is visible.
 *
 * The Library already puts a download on every card. Every OTHER surface that
 * shows the same bytes — the Brand HQ kit, the mark sheets, an agent's avatar —
 * showed them with no way to get them out, which is backwards for a console
 * whose main job is retrieval rather than authoring.
 *
 * Self-contained on purpose: it owns its own busy state and error toast, so a
 * page can drop it next to an image without threading a handler down. Styled
 * from the BRAND tokens (--b-*) rather than the chrome ones, because these
 * surfaces repaint in the brand's palette and a chrome-inked button goes
 * invisible on a light brand.
 */

const BASE: CSSProperties = {
  position: "absolute",
  right: 6,
  top: 6,
  zIndex: 4,
  display: "grid",
  placeItems: "center",
  width: 30,
  height: 30,
  borderRadius: 9,
  border: "1px solid var(--b-hairline)",
  background: "color-mix(in srgb, var(--b-bg) 78%, transparent)",
  color: "var(--b-ink-2)",
  backdropFilter: "blur(6px)",
  cursor: "pointer",
};

function Shell({
  busy,
  disabled,
  label,
  title,
  onClick,
  style,
}: {
  busy: boolean;
  disabled: boolean;
  label: string;
  title: string;
  onClick: () => void;
  style?: CSSProperties;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={label}
      title={title}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onClick();
      }}
      style={{ ...BASE, ...style, opacity: disabled ? 0.4 : 1, cursor: disabled ? "default" : "pointer" }}
    >
      {busy ? <Spinner size={13} /> : <Download size={14} />}
    </button>
  );
}

/** Download a catalogued asset under its human-readable name. */
export function AssetDownloadButton({
  asset,
  style,
}: {
  asset: AssetRecord;
  style?: CSSProperties;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Shell
      busy={busy}
      disabled={busy || !asset.url}
      label={`Download the ${asset.asset_type.replace(/_/g, " ")} for ${asset.brand}`}
      title={asset.url ? "Download this file" : "No file served for this asset"}
      style={style}
      onClick={async () => {
        setBusy(true);
        try {
          await downloadAsset(asset);
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Download failed.");
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}

/**
 * Download an image that is NOT a catalog record — a brand mark or an agent
 * avatar, which are served files without an AssetRecord around them.
 */
export function FileDownloadButton({
  url,
  filename,
  label,
  style,
}: {
  url: string | null | undefined;
  filename: string;
  label: string;
  style?: CSSProperties;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Shell
      busy={busy}
      disabled={busy || !url}
      label={label}
      title={url ? "Download this file" : "No file served"}
      style={style}
      onClick={async () => {
        if (!url) return;
        setBusy(true);
        try {
          await downloadFile(url, filename);
        } catch (err) {
          toast.error(err instanceof Error ? err.message : "Download failed.");
        } finally {
          setBusy(false);
        }
      }}
    />
  );
}
