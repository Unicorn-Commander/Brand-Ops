/**
 * Getting assets OUT of Brand-Ops.
 *
 * The Library is a retrieval surface first: the common job is "give me that
 * file", not "make another one". Everything here is client-side and leans on the
 * existing `/api/assets/file` route the thumbnails already read, so a download
 * carries the same session cookie, the same tenancy scoping, and the same
 * path-traversal guard the image render does. No new backend surface.
 *
 * Two shapes:
 *   - `downloadAsset`   one file, saved under a human-readable name.
 *   - `downloadAssetsAsZip`  many files, packed in the browser into a single
 *     .zip with `<brand>/<asset type>/<name>` folders.
 *
 * The zip is written with the STORE method (no compression). Every asset here is
 * already a compressed image (PNG / JPEG) or a tiny SVG, so DEFLATE would burn
 * CPU for ~0% gain — and STORE keeps this a ~100-line writer with no dependency.
 */
import type { AssetRecord } from "./types";

/** Extensions we can infer when a record's file name carries none. */
const MIME_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/webp": "webp",
  "image/svg+xml": "svg",
  "image/gif": "gif",
  "image/avif": "avif",
};

/** Refuse to build a zip larger than this. Beyond it the tab risks an OOM. */
const ZIP_MAX_BYTES = 1_200 * 1024 * 1024;
/** Refuse more entries than this (the writer emits a non-Zip64 archive). */
const ZIP_MAX_ENTRIES = 2000;

/**
 * The file extension for a record: whatever its stored file already ends in,
 * else one inferred from its MIME type, else "png" (every backend we drive
 * writes a raster image, so that is the safe default rather than a bare name).
 */
export function extensionFor(record: AssetRecord): string {
  const dot = record.file.lastIndexOf(".");
  if (dot > 0 && dot < record.file.length - 1) {
    const ext = record.file.slice(dot + 1).toLowerCase();
    if (/^[a-z0-9]{1,5}$/.test(ext)) return ext;
  }
  const mime = (record.mime ?? "").toLowerCase();
  return MIME_EXT[mime] ?? "png";
}

/** Strip anything a filesystem (or a zip reader) would choke on. */
function slugSegment(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
}

/**
 * A human-readable download name for one asset:
 *   `<brand>-<asset type>[-<variant>][-<width>x<height>].<ext>`
 * e.g. `unicorn-commander-app_icon-1024x1024.png`.
 *
 * Built from catalog fields rather than the stored name, because the stored name
 * is a generation timestamp (`2026-07-09-07-05-18-social_card.jpg`) that tells a
 * human nothing about which brand it belongs to — the single most useful thing
 * once the file is sitting in ~/Downloads next to forty others.
 */
export function downloadNameFor(record: AssetRecord): string {
  const parts = [
    slugSegment(record.brand),
    slugSegment(record.asset_type),
    record.variant ? slugSegment(record.variant) : "",
  ].filter(Boolean);
  if (record.size.length === 2 && record.size[0] > 0 && record.size[1] > 0) {
    parts.push(`${record.size[0]}x${record.size[1]}`);
  }
  const stem = parts.join("-") || slugSegment(record.file) || "asset";
  return `${stem}.${extensionFor(record)}`;
}

/** The `<brand>/<asset type>/<name>` path an asset takes inside a zip. */
export function zipPathFor(record: AssetRecord): string {
  const brand = slugSegment(record.brand) || "unfiled";
  const type = slugSegment(record.asset_type) || "asset";
  return `${brand}/${type}/${downloadNameFor(record)}`;
}

/** Make every path unique by suffixing `-2`, `-3`, ... before the extension. */
function dedupe(paths: string[]): string[] {
  const seen = new Map<string, number>();
  return paths.map((path) => {
    const n = (seen.get(path) ?? 0) + 1;
    seen.set(path, n);
    if (n === 1) return path;
    const dot = path.lastIndexOf(".");
    return dot > 0
      ? `${path.slice(0, dot)}-${n}${path.slice(dot)}`
      : `${path}-${n}`;
  });
}

/** Hand a blob to the browser's downloader under `filename`. */
function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next frame: revoking synchronously can race the download in
  // Safari/WebKit, which reads the object URL after the click returns.
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/**
 * Fetch one asset's bytes.
 *
 * Behind oauth2-proxy an expired session answers a plain GET with a redirect to
 * the identity provider, which a `fetch` follows cross-origin and fails on (or
 * worse, returns an HTML login page). Both cases would otherwise save a file
 * called `brand-logo.png` containing HTML, so we check the content type and
 * raise instead — a wrong file is worse than an honest error.
 */
async function fetchServed(url: string | null | undefined): Promise<Response> {
  if (!url) {
    throw new Error("This asset has no served file.");
  }
  let res: Response;
  try {
    res = await fetch(url, { credentials: "same-origin" });
  } catch {
    throw new Error("Could not reach the server. Reload and try again.");
  }
  if (!res.ok) {
    throw new Error(
      res.status === 404
        ? "The file is missing on the server."
        : `Download failed (${res.status}).`,
    );
  }
  const type = res.headers.get("content-type") ?? "";
  if (type.includes("text/html")) {
    throw new Error("Your session expired. Reload the page and sign in again.");
  }
  return res;
}

async function fetchAsset(record: AssetRecord): Promise<Response> {
  return fetchServed(record.url);
}

/** Download one asset under its human-readable name. Throws on failure. */
export async function downloadAsset(record: AssetRecord): Promise<void> {
  const res = await fetchAsset(record);
  saveBlob(await res.blob(), downloadNameFor(record));
}

/**
 * Download any served file under a caller-chosen name.
 *
 * For the images that are NOT catalog records — a brand mark, an agent avatar —
 * so those surfaces get the same "give me the file" affordance, through the same
 * expired-session and 404 guards rather than a bare `<a download>` (which would
 * happily save a login page as a .png).
 */
export async function downloadFile(
  url: string | null | undefined,
  filename: string,
): Promise<void> {
  const res = await fetchServed(url);
  saveBlob(await res.blob(), filename);
}

/**
 * A safe download file name: `<stem>.<ext>`, where the extension is taken from
 * the served URL when it has a plausible one and defaults to png otherwise.
 */
export function fileNameFor(stem: string, url: string | null | undefined): string {
  const clean = slugSegment(stem) || "file";
  const path = (url ?? "").split(/[?#]/)[0];
  const dot = path.lastIndexOf(".");
  const ext = dot > 0 ? path.slice(dot + 1).toLowerCase() : "";
  return `${clean}.${/^[a-z0-9]{1,5}$/.test(ext) ? ext : "png"}`;
}

// ---------------------------------------------------------------------------
// Minimal ZIP (STORE) writer
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i += 1) {
    let c = i;
    for (let k = 0; k < 8; k += 1) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) {
    c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

/** MS-DOS packed (time, date) pair, as the local/central headers want them. */
function dosStamp(d: Date): { time: number; date: number } {
  return {
    time:
      (Math.floor(d.getSeconds() / 2) & 0x1f) |
      ((d.getMinutes() & 0x3f) << 5) |
      ((d.getHours() & 0x1f) << 11),
    date:
      (d.getDate() & 0x1f) |
      (((d.getMonth() + 1) & 0x0f) << 5) |
      ((Math.max(0, d.getFullYear() - 1980) & 0x7f) << 9),
  };
}

interface ZipEntry {
  path: string;
  bytes: Uint8Array;
  modified: Date;
}

/**
 * A byte view as a Blob part.
 *
 * TypeScript 5.7 made `Uint8Array` generic over its backing buffer, so a plain
 * `Uint8Array` (which could in principle sit on a `SharedArrayBuffer`) no longer
 * satisfies `BlobPart`. Every view built here comes from a freshly allocated
 * `ArrayBuffer`, so the narrowing is sound; the cast keeps this compiling on
 * both sides of that change without copying the bytes.
 */
function asPart(bytes: Uint8Array): BlobPart {
  return bytes as unknown as BlobPart;
}

/**
 * Pack entries into an uncompressed (STORE) zip Blob.
 *
 * Flag bit 11 is set on every entry so readers decode the file names as UTF-8,
 * which matters as soon as a brand slug is not pure ASCII.
 */
function buildZip(entries: ZipEntry[]): Blob {
  const encoder = new TextEncoder();
  const parts: BlobPart[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = encoder.encode(entry.path);
    const crc = crc32(entry.bytes);
    const size = entry.bytes.length;
    const { time, date } = dosStamp(entry.modified);

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true); // version needed
    local.setUint16(6, 0x0800, true); // UTF-8 names
    local.setUint16(8, 0, true); // method: store
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, size, true);
    local.setUint32(22, size, true);
    local.setUint16(26, name.length, true);
    local.setUint16(28, 0, true); // extra length
    parts.push(local.buffer, asPart(name), asPart(entry.bytes));

    const cd = new DataView(new ArrayBuffer(46));
    cd.setUint32(0, 0x02014b50, true);
    cd.setUint16(4, 20, true); // version made by
    cd.setUint16(6, 20, true); // version needed
    cd.setUint16(8, 0x0800, true);
    cd.setUint16(10, 0, true);
    cd.setUint16(12, time, true);
    cd.setUint16(14, date, true);
    cd.setUint32(16, crc, true);
    cd.setUint32(20, size, true);
    cd.setUint32(24, size, true);
    cd.setUint16(28, name.length, true);
    cd.setUint16(30, 0, true); // extra
    cd.setUint16(32, 0, true); // comment
    cd.setUint16(34, 0, true); // disk number
    cd.setUint16(36, 0, true); // internal attrs
    cd.setUint32(38, 0, true); // external attrs
    cd.setUint32(42, offset, true);
    const cdBytes = new Uint8Array(cd.byteLength + name.length);
    cdBytes.set(new Uint8Array(cd.buffer), 0);
    cdBytes.set(name, cd.byteLength);
    central.push(cdBytes);

    offset += 30 + name.length + size;
  }

  const centralSize = central.reduce((sum, c) => sum + c.length, 0);
  const eocd = new DataView(new ArrayBuffer(22));
  eocd.setUint32(0, 0x06054b50, true);
  eocd.setUint16(4, 0, true);
  eocd.setUint16(6, 0, true);
  eocd.setUint16(8, entries.length, true);
  eocd.setUint16(10, entries.length, true);
  eocd.setUint32(12, centralSize, true);
  eocd.setUint32(16, offset, true);
  eocd.setUint16(20, 0, true);

  return new Blob([...parts, ...central.map(asPart), eocd.buffer], {
    type: "application/zip",
  });
}

export interface ZipProgress {
  /** How many assets have been fetched so far. */
  done: number;
  /** How many were asked for. */
  total: number;
}

export interface ZipOutcome {
  /** Assets successfully packed. */
  packed: number;
  /** Assets that could not be fetched (they are simply absent from the zip). */
  failed: number;
}

/**
 * Fetch every record and save them as one zip named `zipName`.
 *
 * Fetches run a few at a time: fully serial is needlessly slow over 50 assets,
 * and fully parallel would open 50 sockets against a single-worker API. A record
 * that fails is skipped rather than aborting the archive — a 49-of-50 zip beats
 * no zip — and the count comes back so the caller can say so out loud.
 */
export async function downloadAssetsAsZip(
  records: AssetRecord[],
  zipName: string,
  onProgress?: (progress: ZipProgress) => void,
): Promise<ZipOutcome> {
  const servable = records.filter((r) => r.url);
  if (servable.length === 0) {
    throw new Error("None of the selected assets have a served file.");
  }
  if (servable.length > ZIP_MAX_ENTRIES) {
    throw new Error(
      `That is ${servable.length} files. Filter down to ${ZIP_MAX_ENTRIES} or fewer and try again.`,
    );
  }

  const paths = dedupe(servable.map(zipPathFor));
  const entries: (ZipEntry | null)[] = new Array(servable.length).fill(null);
  let done = 0;
  let bytes = 0;
  let failed = 0;

  const CONCURRENCY = 5;
  let cursor = 0;
  const worker = async () => {
    for (;;) {
      const i = cursor;
      cursor += 1;
      if (i >= servable.length) return;
      const record = servable[i];
      try {
        const res = await fetchAsset(record);
        const buf = new Uint8Array(await res.arrayBuffer());
        bytes += buf.length;
        if (bytes > ZIP_MAX_BYTES) {
          throw new Error(
            "That selection is too large to zip in the browser. Download it in smaller batches.",
          );
        }
        const stamp = record.created_at ? new Date(record.created_at) : new Date();
        entries[i] = {
          path: paths[i],
          bytes: buf,
          modified: Number.isNaN(stamp.getTime()) ? new Date() : stamp,
        };
      } catch (err) {
        // A size blow-out is fatal for the whole archive; a single bad file is not.
        if (err instanceof Error && err.message.includes("too large")) throw err;
        failed += 1;
      }
      done += 1;
      onProgress?.({ done, total: servable.length });
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(CONCURRENCY, servable.length) }, worker),
  );

  const packed = entries.filter((e): e is ZipEntry => e !== null);
  if (packed.length === 0) {
    throw new Error("Every file failed to download.");
  }
  saveBlob(buildZip(packed), zipName.endsWith(".zip") ? zipName : `${zipName}.zip`);
  return { packed: packed.length, failed };
}

/** A stable, descriptive name for a zip of `records`. */
export function zipNameFor(records: AssetRecord[], label?: string): string {
  const brands = new Set(records.map((r) => r.brand));
  const scope =
    label ??
    (brands.size === 1 ? slugSegment([...brands][0]) : `${brands.size}-brands`);
  const day = new Date().toISOString().slice(0, 10);
  return `brand-ops-${scope || "assets"}-${day}.zip`;
}
