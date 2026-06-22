"""Deterministic text-on-brand card compositing.

Image models render legible text only by luck: gpt-image-1 nailed one headline
and misspelled the next. For brand-bearing cards (share images, social posts)
that is not good enough, so this module composes them deterministically instead
of asking a model to draw text. It renders the brand's real fonts and exact
colors over a brand-colored background and alpha-composites the brand mark, so
the copy is always correct, on brand, and pixel-identical run to run. That is
the same "auditable brand truth" the compliance engine embodies, applied to
production.

The layouts cover the suite's needs today:

* ``og_image`` (1200x630): a left text column (eyebrow + headline) with the mark
  on the right. The link-preview / share card.
* ``social_card`` (1080x1080): the mark centered over the wordmark and a tagline.
* ``launcher_tile`` (512x512): just the brand mark on a branded square, sized to
  read at ~64px. The Ops-Center launcher tile (and any app-grid icon). No text,
  since copy would not read at tile size.

Fonts are bundled under ``assets/fonts`` (Inter + Space Grotesk, both OFL). An
unknown brand font falls back to Inter, and a missing font file falls back to
Pillow's default, so a render never crashes.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import Brand

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_DEFAULT_FONT = "Inter.ttf"
# Map a brand font family (lower-cased) to a bundled file. Unknown -> Inter.
_FONT_FILES = {
    "inter": "Inter.ttf",
    "space grotesk": "SpaceGrotesk.ttf",
}

# Supported card sizes.
_SIZES = {"og_image": (1200, 630), "social_card": (1080, 1080), "launcher_tile": (512, 512)}


def supported(asset_type: str) -> bool:
    """True when this asset type has a compositing layout."""
    return asset_type in _SIZES


def _hex_to_rgb(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not value:
        return fallback
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return fallback


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _luminance(rgb: tuple[int, int, int]) -> float:
    """Perceptual relative luminance (Rec. 709), 0..255."""
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Cheap contrast proxy: absolute luminance difference between two colors."""
    return abs(_luminance(a) - _luminance(b))


def _load_font(
    family: str | None, size: int, weights: list[str]
) -> ImageFont.FreeTypeFont:
    """Load a brand font at ``size``, preferring the first available ``weights``.

    Unknown family -> Inter; missing file -> Pillow's default (never raises).
    """
    fname = _FONT_FILES.get((family or "").strip().lower(), _DEFAULT_FONT)
    try:
        font = ImageFont.truetype(str(_FONT_DIR / fname), size)
    except Exception:  # noqa: BLE001 - any load failure degrades gracefully
        try:
            return ImageFont.load_default(size)
        except Exception:  # noqa: BLE001 - very old Pillow
            return ImageFont.load_default()
    for weight in weights:
        try:
            font.set_variation_by_name(weight)
            break
        except Exception:  # noqa: BLE001 - weight not in this font; try the next
            continue
    return font


def _brand_background(
    size: tuple[int, int],
    bg: tuple[int, int, int],
    primary: tuple[int, int, int],
    accent: tuple[int, int, int],
    alpha: float = 0.13,
) -> Image.Image:
    """A dark brand field with a faint diagonal primary->accent wash.

    Built from a 2x2 corner gradient resized up (fast, smooth) and blended onto
    the near-black base at low alpha, for an on-brand "aurora" feel that keeps
    text readable.
    """
    small = Image.new("RGB", (2, 2))
    small.putpixel((0, 0), primary)
    small.putpixel((1, 0), _mix(primary, accent, 0.5))
    small.putpixel((0, 1), _mix(primary, accent, 0.5))
    small.putpixel((1, 1), accent)
    wash = small.resize(size, Image.BILINEAR)
    return Image.blend(Image.new("RGB", size, bg), wash, alpha)


def _wrap(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int
) -> list[str]:
    """Greedy word-wrap ``text`` to lines no wider than ``max_w`` pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _paste_mark(card: Image.Image, mark_path: Path | None, box: tuple[int, int, int, int]) -> None:
    """Fit the brand mark inside ``box`` (l, t, r, b) and alpha-composite it centered."""
    if not mark_path or not Path(mark_path).is_file():
        return
    try:
        mark = Image.open(mark_path).convert("RGBA")
    except Exception:  # noqa: BLE001 - a bad mark must not break the card
        return
    bl, bt, br, bb = box
    mark.thumbnail((br - bl, bb - bt), Image.LANCZOS)
    x = bl + (br - bl - mark.width) // 2
    y = bt + (bb - bt - mark.height) // 2
    card.paste(mark, (x, y), mark)


def compose_card(
    brand: Brand,
    asset_type: str,
    *,
    message: str | None = None,
    mark_path: Path | None = None,
) -> bytes:
    """Render an on-brand card with real text and the brand mark, as PNG bytes.

    ``message`` is the main copy (the headline for og_image, the tagline for
    social_card); it defaults to the brand tagline. ``mark_path`` is the brand
    mark to composite (a transparent logo works best).
    """
    if asset_type not in _SIZES:
        raise ValueError(f"no card layout for {asset_type!r}; have {sorted(_SIZES)}")

    size = _SIZES[asset_type]
    colors = brand.visual.colors
    bg = _hex_to_rgb(colors.background, (10, 10, 15))
    primary = _hex_to_rgb(colors.primary, (182, 110, 255))
    accent = _hex_to_rgb(colors.accent, (0, 212, 255))
    fg = _hex_to_rgb(getattr(colors, "foreground", None), (240, 240, 245))
    muted = _hex_to_rgb(getattr(colors, "muted", None), (160, 160, 184))

    typography = brand.visual.typography
    display_family = getattr(typography, "display_font", None)
    body_family = getattr(typography, "primary_font", None)

    wordmark = brand.brand.display_name
    text = message or brand.brand.tagline or ""

    # The eyebrow/label uses whichever of accent/primary reads better on the
    # background, so it never lands low-contrast (e.g. navy-on-navy).
    eyebrow = accent if _contrast(accent, bg) >= _contrast(primary, bg) else primary
    style = {
        "primary": primary,
        "accent": accent,
        "eyebrow": eyebrow,
        "fg": fg,
        "muted": muted,
        "display": display_family,
        "body": body_family,
        "wordmark": wordmark,
    }
    card = _brand_background(size, bg, primary, accent)
    draw = ImageDraw.Draw(card)
    if asset_type == "og_image":
        _compose_og(draw, card, size, style, text, mark_path)
    elif asset_type == "social_card":
        _compose_social(draw, card, size, style, text, mark_path)
    else:  # launcher_tile: the mark on a branded square, no text
        _compose_tile(card, size, mark_path)

    buffer = io.BytesIO()
    card.save(buffer, "PNG")
    return buffer.getvalue()


def _compose_og(draw, card, size, style, text, mark_path):
    width, height = size
    margin = 72
    text_w = 640

    # The mark sits on the right, vertically centered.
    box = (width - margin - 380, (height - 380) // 2, width - margin, (height + 380) // 2)
    _paste_mark(card, mark_path, box)

    eyebrow_font = _load_font(style["display"], 28, ["SemiBold", "Bold", "Medium"])
    headline_font = _load_font(style["display"], 62, ["Bold", "SemiBold"])

    lines = _wrap(draw, text, headline_font, text_w)
    hl_h = _line_height(headline_font)
    eyebrow_h = _line_height(eyebrow_font)
    gap = 24
    block_h = eyebrow_h + gap + hl_h * len(lines)
    y = (height - block_h) // 2

    draw.text((margin, y), style["wordmark"].upper(), font=eyebrow_font, fill=style["eyebrow"])
    y += eyebrow_h + gap
    for line in lines:
        draw.text((margin, y), line, font=headline_font, fill=style["fg"])
        y += hl_h


def _compose_tile(card, size, mark_path):
    """A launcher tile: the brand mark centered on the branded square, no text.

    The mark fills the tile inside a ~16% margin on every side, so it stays
    centered and legible when the launcher renders it small (~64px). The branded
    background (the aurora wash over the brand background color) is already on the
    card, so a transparent mark reads on brand; the tile itself is a full square
    (the launcher applies its own corner rounding).
    """
    width, height = size
    inset_x = round(width * 0.16)
    inset_y = round(height * 0.16)
    _paste_mark(card, mark_path, (inset_x, inset_y, width - inset_x, height - inset_y))


def _compose_social(draw, card, size, style, text, mark_path):
    width, height = size
    # Mark centered in the upper half.
    box_size = 460
    box = ((width - box_size) // 2, 150, (width + box_size) // 2, 150 + box_size)
    _paste_mark(card, mark_path, box)

    wordmark_font = _load_font(style["display"], 76, ["Bold", "SemiBold"])
    tagline_font = _load_font(style["body"], 38, ["Regular", "Medium"])

    cx = width // 2
    draw.text((cx, 700), style["wordmark"], font=wordmark_font, fill=style["fg"], anchor="ma")

    lines = _wrap(draw, text, tagline_font, width - 220)
    y = 700 + _line_height(wordmark_font) + 28
    for line in lines:
        draw.text((cx, y), line, font=tagline_font, fill=style["muted"], anchor="ma")
        y += _line_height(tagline_font)
