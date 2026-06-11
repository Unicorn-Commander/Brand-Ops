/**
 * The Template Studio canvas renderer.
 *
 * `renderTemplate` draws a locked TemplateDef onto a 2D canvas using the active
 * brand's tokens. It is PURE and DETERMINISTIC: same inputs (def + tokens + mark
 * image + values) always produce the same pixels, and it makes no network calls.
 * The page loads the brand tokens and the mark image; this module only draws.
 *
 * Why a renderer instead of an image model: the layout, fonts, colors, and mark
 * are fixed by the TemplateDef. A non-designer (or an agent) edits only the text
 * slot CONTENT, so every export is on-brand by construction. Colors and fonts
 * reference brand TOKEN names, resolved here against the BrandTokens, never raw
 * hex baked into the layout.
 */
import type { BrandTokens, TemplateDef, TemplateSlot } from "../../lib/types";

/**
 * The font families the app self-hosts (see web/src/index.css). Used as the
 * always-present fallback so text renders correctly even before a brand's own
 * family loads (or when it is not installed). The role -> family mapping mirrors
 * tailwind.config.js: display = Space Grotesk, body = Inter.
 */
const SELF_HOSTED_DISPLAY = '"Space Grotesk Variable", "Space Grotesk"';
const SELF_HOSTED_BODY = '"InterVariable", "Inter"';
const SANS_FALLBACK = "system-ui, -apple-system, Segoe UI, sans-serif";

/** The font token name a slot's `font` role resolves to. */
function fontTokenName(role: "display" | "body"): string {
  return role === "display" ? "font/display" : "font/body";
}

/**
 * Build the CSS `font` shorthand for a text slot. The brand's named family (from
 * the font token) goes first, then the matching self-hosted family, then a
 * generic sans, so the canvas always has a real font to draw with. A multi-word
 * family is quoted. Weight is fixed at 600 for display headings and 400 for body
 * so the locked look is consistent across brands.
 */
function fontStringFor(
  slot: TemplateSlot,
  tokens: BrandTokens,
  scale: number,
): string {
  const role = slot.font ?? "body";
  const sizePx = Math.max(1, Math.round((slot.size ?? 32) * scale));
  const weight = role === "display" ? 600 : 400;

  const tokenName = fontTokenName(role);
  const tokenFamily = tokens.typography.find((t) => t.name === tokenName)?.value;
  const selfHosted = role === "display" ? SELF_HOSTED_DISPLAY : SELF_HOSTED_BODY;

  const families: string[] = [];
  if (tokenFamily) {
    families.push(/\s/.test(tokenFamily) ? `"${tokenFamily}"` : tokenFamily);
  }
  families.push(selfHosted, SANS_FALLBACK);

  return `${weight} ${sizePx}px ${families.join(", ")}`;
}

/**
 * Resolve a color-token name (e.g. "text/foreground") to a hex string from the
 * brand tokens. Returns the fallback when the brand does not define that token,
 * so a render never throws on a missing optional color (a brand may omit
 * text/muted). Color tokens carry `value` (hex); we use that directly.
 */
function resolveColor(
  tokenName: string | null,
  tokens: BrandTokens,
  fallback: string,
): string {
  if (!tokenName) return fallback;
  const token = tokens.color.find((c) => c.name === tokenName);
  return token?.value ?? fallback;
}

/**
 * Wrap `text` to a pixel `maxWidth` using the context's current font. Splits on
 * existing newlines first, then greedily on spaces. A single word wider than the
 * line is hard-broken by characters so it never overflows the slot. `ctx.font`
 * must already be set (measureText reads it).
 */
function wrapLines(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
): string[] {
  const out: string[] = [];
  // Honor explicit line breaks the author/editor typed.
  for (const paragraph of text.split("\n")) {
    if (paragraph === "") {
      out.push("");
      continue;
    }
    const words = paragraph.split(/\s+/).filter((w) => w.length > 0);
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (ctx.measureText(candidate).width <= maxWidth || line === "") {
        // The single word may itself exceed maxWidth: hard-break it.
        if (!line && ctx.measureText(word).width > maxWidth) {
          const broken = hardBreak(ctx, word, maxWidth);
          out.push(...broken.slice(0, -1));
          line = broken[broken.length - 1];
        } else {
          line = candidate;
        }
      } else {
        out.push(line);
        if (ctx.measureText(word).width > maxWidth) {
          const broken = hardBreak(ctx, word, maxWidth);
          out.push(...broken.slice(0, -1));
          line = broken[broken.length - 1];
        } else {
          line = word;
        }
      }
    }
    out.push(line);
  }
  return out;
}

/** Break one long token into character runs that each fit `maxWidth`. */
function hardBreak(
  ctx: CanvasRenderingContext2D,
  word: string,
  maxWidth: number,
): string[] {
  const lines: string[] = [];
  let chunk = "";
  for (const ch of word) {
    const candidate = chunk + ch;
    if (ctx.measureText(candidate).width <= maxWidth || chunk === "") {
      chunk = candidate;
    } else {
      lines.push(chunk);
      chunk = ch;
    }
  }
  if (chunk) lines.push(chunk);
  return lines;
}

/** Paint the template background: the brand gradient or a solid token color. */
function drawBackground(
  ctx: CanvasRenderingContext2D,
  def: TemplateDef,
  tokens: BrandTokens,
): void {
  const { width, height } = def;
  if (def.background.kind === "brand-gradient") {
    // A diagonal gradient brand/primary -> brand/accent, matching the suite's
    // accent gradient direction. Falls back to the suite purple/cyan when a
    // brand somehow lacks either token, so the panel is never blank.
    const start = resolveColor("brand/primary", tokens, "#b66eff");
    const end = resolveColor("brand/accent", tokens, "#00d4ff");
    const grad = ctx.createLinearGradient(0, 0, width, height);
    grad.addColorStop(0, start);
    grad.addColorStop(1, end);
    ctx.fillStyle = grad;
  } else {
    // Solid: the named token, defaulting to a near-black surface.
    ctx.fillStyle = resolveColor(def.background.color, tokens, "#0a0a0f");
  }
  ctx.fillRect(0, 0, width, height);
}

/**
 * Draw the brand mark into a mark slot, contained within the slot's width
 * (`w` fraction of the canvas) while preserving the image's aspect ratio. The
 * slot anchor (x,y) is the mark's CENTER, so a centered mark stays centered as
 * the canvas scales. No-op when the mark image is missing or not yet loaded.
 */
function drawMark(
  ctx: CanvasRenderingContext2D,
  slot: TemplateSlot,
  def: TemplateDef,
  mark: CanvasImageSource | null,
): void {
  if (!mark) return;
  // Natural size: HTMLImageElement exposes naturalWidth/Height; guard for 0.
  const img = mark as HTMLImageElement;
  const natW = img.naturalWidth || (img.width as number) || 0;
  const natH = img.naturalHeight || (img.height as number) || 0;
  if (natW <= 0 || natH <= 0) return;

  const boxW = slot.w * def.width;
  // Fit width to boxW; height follows the aspect ratio.
  const drawW = boxW;
  const drawH = (natH / natW) * drawW;
  const cx = slot.x * def.width;
  const cy = slot.y * def.height;
  ctx.drawImage(img, cx - drawW / 2, cy - drawH / 2, drawW, drawH);
}

/**
 * Draw one text slot: the locked font/size/weight, the token color, the slot
 * alignment, anchored at the fractional (x,y), wrapped to the slot width. `y` is
 * the TOP of the text block (textBaseline "top"); lines stack downward at a
 * fixed 1.2x leading. Empty content draws nothing.
 */
function drawText(
  ctx: CanvasRenderingContext2D,
  slot: TemplateSlot,
  def: TemplateDef,
  tokens: BrandTokens,
  scale: number,
  value: string,
): void {
  const text = value.trim();
  if (!text) return;

  ctx.font = fontStringFor(slot, tokens, scale);
  ctx.fillStyle = resolveColor(slot.color, tokens, "#f5f5fa");
  ctx.textBaseline = "top";

  const align = slot.align ?? "left";
  ctx.textAlign = align;

  const maxWidth = slot.w * def.width;
  const lines = wrapLines(ctx, text, maxWidth);

  const sizePx = (slot.size ?? 32) * scale;
  const lineHeight = sizePx * 1.2;

  // The anchor x depends on alignment: left -> left edge of the box, center ->
  // box center, right -> right edge. The box starts at slot.x and is maxWidth
  // wide, so the anchor stays within the intended column.
  const left = slot.x * def.width;
  let anchorX = left;
  if (align === "center") anchorX = left + maxWidth / 2;
  else if (align === "right") anchorX = left + maxWidth;

  let y = slot.y * def.height;
  for (const line of lines) {
    ctx.fillText(line, anchorX, y);
    y += lineHeight;
  }
}

/**
 * Render a locked template onto a canvas using the active brand's tokens.
 *
 * Accepts either an HTMLCanvasElement (it sizes the canvas to the def and grabs
 * the 2D context) or a 2D context already sized to def.width x def.height. Draws
 * the background, every mark slot (preserving aspect ratio), then every text
 * slot (locked font/size/color/alignment, word-wrapped) at its fractional
 * position. `values` maps a slot key to its current text; a missing key falls
 * back to the slot's `default`.
 *
 * Deterministic and side-effect free apart from the canvas pixels. Make sure
 * `document.fonts.ready` has resolved before the first call so the brand /
 * self-hosted families are loaded and text measures + draws correctly.
 */
export function renderTemplate(
  target: HTMLCanvasElement | CanvasRenderingContext2D,
  def: TemplateDef,
  tokens: BrandTokens,
  markImage: CanvasImageSource | null,
  values: Record<string, string>,
): void {
  let ctx: CanvasRenderingContext2D | null;
  if (target instanceof HTMLCanvasElement) {
    if (target.width !== def.width) target.width = def.width;
    if (target.height !== def.height) target.height = def.height;
    ctx = target.getContext("2d");
  } else {
    ctx = target;
  }
  if (!ctx) return;

  // The geometry is authored at full canvas scale, so font sizes are already in
  // canvas pixels. `scale` stays 1 here; it exists so a caller could render into
  // a smaller backing buffer in the future without changing the slot math.
  const scale = 1;

  ctx.clearRect(0, 0, def.width, def.height);
  drawBackground(ctx, def, tokens);

  for (const slot of def.slots) {
    if (slot.kind === "mark") {
      drawMark(ctx, slot, def, markImage);
    } else {
      const value = values[slot.key] ?? slot.default ?? "";
      drawText(ctx, slot, def, tokens, scale, value);
    }
  }
}
