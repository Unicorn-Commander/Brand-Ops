import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  downloadAssetsAsZip,
  downloadNameFor,
  extensionFor,
  zipNameFor,
  zipPathFor,
} from "./download";
import type { AssetRecord } from "./types";

function record(over: Partial<AssetRecord> = {}): AssetRecord {
  return {
    brand: "unicorn-commander",
    asset_type: "app_icon",
    variant: null,
    backend: "litellm",
    model: null,
    mime: "image/png",
    size: [1024, 1024],
    transparent: null,
    cost_cents: null,
    created_at: "2026-07-09T07:04:46Z",
    file: "2026-07-09-07-04-46-app_icon.png",
    kind: "generated",
    url: "/api/assets/file?brand=unicorn-commander&asset=app_icon&file=a.png",
    prompt: null,
    reference_images: [],
    ...over,
  };
}

describe("extensionFor", () => {
  it("prefers the stored file's own extension", () => {
    expect(extensionFor(record({ file: "x.jpg", mime: "image/png" }))).toBe("jpg");
  });

  it("falls back to the MIME type when the file has no extension", () => {
    expect(extensionFor(record({ file: "no-extension", mime: "image/svg+xml" })))
      .toBe("svg");
  });

  it("falls back to png when neither is usable", () => {
    expect(extensionFor(record({ file: "bare", mime: null }))).toBe("png");
  });
});

describe("downloadNameFor", () => {
  it("names a file after its brand, type, and pixel size", () => {
    expect(downloadNameFor(record())).toBe(
      "unicorn-commander-app_icon-1024x1024.png",
    );
  });

  it("includes the variant when there is one", () => {
    expect(downloadNameFor(record({ variant: "color" }))).toBe(
      "unicorn-commander-app_icon-color-1024x1024.png",
    );
  });

  it("drops the dimensions when the catalog has no size", () => {
    expect(downloadNameFor(record({ size: [] }))).toBe(
      "unicorn-commander-app_icon.png",
    );
  });

  it("strips characters a filesystem would object to", () => {
    const name = downloadNameFor(record({ brand: "Acme / Co.", size: [] }));
    expect(name).toBe("acme-co-app_icon.png");
  });
});

describe("zipPathFor", () => {
  it("files each asset under <brand>/<type>/", () => {
    expect(zipPathFor(record())).toBe(
      "unicorn-commander/app_icon/unicorn-commander-app_icon-1024x1024.png",
    );
  });
});

describe("zipNameFor", () => {
  it("names a single-brand zip after that brand", () => {
    expect(zipNameFor([record()])).toMatch(/^brand-ops-unicorn-commander-\d{4}-\d{2}-\d{2}\.zip$/);
  });

  it("counts the brands when a selection spans several", () => {
    const zip = zipNameFor([record(), record({ brand: "meeting-ops" })]);
    expect(zip).toContain("2-brands");
  });
});

// ---------------------------------------------------------------------------
// The zip writer. These assert the ARCHIVE BYTES, not just that the call
// resolved: a zip that "downloads" but that no unzipper will open is the exact
// failure this replaces.
// ---------------------------------------------------------------------------

/** Read a little-endian unsigned int of `len` bytes at `at`. */
function u(bytes: Uint8Array, at: number, len: number): number {
  let v = 0;
  for (let i = len - 1; i >= 0; i -= 1) v = v * 256 + bytes[at + i];
  return v;
}

let saved: { blob: Blob; name: string } | null = null;

// jsdom 25's Blob predates `Blob.prototype.arrayBuffer` (every browser has had
// it since 2019), so reading back the archive the writer built needs a shim.
// Test harness only; the module never calls it.
if (typeof Blob.prototype.arrayBuffer !== "function") {
  Blob.prototype.arrayBuffer = function arrayBuffer(this: Blob) {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

beforeEach(() => {
  saved = null;
  // jsdom implements neither object URLs nor a real anchor click, so stand both
  // up: createObjectURL/revokeObjectURL as no-ops, and a click that records what
  // the module was about to hand the browser.
  URL.createObjectURL = (() => "blob:test") as typeof URL.createObjectURL;
  URL.revokeObjectURL = (() => undefined) as typeof URL.revokeObjectURL;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    saved = { blob: lastBlob!, name: this.download };
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

let lastBlob: Blob | null = null;

/** Serve deterministic bytes for every asset URL. */
function stubFetch(bodies: Record<string, Uint8Array>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = bodies[url];
      if (!body) {
        return new Response(null, { status: 404 });
      }
      return new Response(body as unknown as BodyInit, {
        status: 200,
        headers: { "content-type": "image/png" },
      });
    }),
  );
}

describe("downloadAssetsAsZip", () => {
  it("writes a readable STORE archive with one entry per asset", async () => {
    const a = record({ url: "/a", file: "a.png", brand: "brand-a" });
    const b = record({ url: "/b", file: "b.png", brand: "brand-b" });
    const bytesA = new Uint8Array([1, 2, 3, 4, 5]);
    const bytesB = new Uint8Array([9, 9]);
    stubFetch({ "/a": bytesA, "/b": bytesB });

    // Capture the Blob the writer builds (the click spy reads it).
    const RealBlob = globalThis.Blob;
    vi.stubGlobal(
      "Blob",
      class extends RealBlob {
        constructor(parts?: BlobPart[], opts?: BlobPropertyBag) {
          super(parts, opts);
          lastBlob = this as unknown as Blob;
        }
      },
    );

    const outcome = await downloadAssetsAsZip([a, b], "pack.zip");
    expect(outcome).toEqual({ packed: 2, failed: 0 });
    expect(saved).not.toBeNull();
    expect(saved!.name).toBe("pack.zip");

    const zip = new Uint8Array(await saved!.blob.arrayBuffer());
    // Local file header signature at offset 0.
    expect(u(zip, 0, 4)).toBe(0x04034b50);
    // STORE (method 0) and the UTF-8 name flag.
    expect(u(zip, 8, 2)).toBe(0);
    expect(u(zip, 6, 2)).toBe(0x0800);
    // Compressed and uncompressed sizes agree, and match the payload.
    expect(u(zip, 18, 4)).toBe(bytesA.length);
    expect(u(zip, 22, 4)).toBe(bytesA.length);

    // The end-of-central-directory record closes the file and counts entries.
    const eocd = zip.length - 22;
    expect(u(zip, eocd, 4)).toBe(0x06054b50);
    expect(u(zip, eocd + 10, 2)).toBe(2);
    // The central directory offset must point at a central header signature.
    const cdOffset = u(zip, eocd + 16, 4);
    expect(u(zip, cdOffset, 4)).toBe(0x02014b50);

    // Both names are stored, foldered by brand.
    const text = new TextDecoder().decode(zip);
    expect(text).toContain("brand-a/app_icon/");
    expect(text).toContain("brand-b/app_icon/");
  });

  it("packs what it can and reports what it could not read", async () => {
    const ok = record({ url: "/ok", file: "ok.png" });
    const gone = record({ url: "/gone", file: "gone.png", asset_type: "logo" });
    stubFetch({ "/ok": new Uint8Array([7]) });

    const outcome = await downloadAssetsAsZip([ok, gone], "partial.zip");
    expect(outcome).toEqual({ packed: 1, failed: 1 });
  });

  it("refuses a selection with no served files", async () => {
    stubFetch({});
    await expect(
      downloadAssetsAsZip([record({ url: null })], "empty.zip"),
    ).rejects.toThrow(/served file/i);
  });
});
