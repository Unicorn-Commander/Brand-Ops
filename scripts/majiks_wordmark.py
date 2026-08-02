#!/usr/bin/env python3
"""Render a Majiks-family wordmark lockup deterministically (no model, $0).

Reproduces the shipped Majiks lockup geometry -- "Majiks" set in Space Grotesk
Bold with one accent-colored terminal glyph, a letter-spaced product subline,
and two thin flanking rules -- at the sub-brand's own palette. The shipped
family SVGs use "Majik's" with a colored apostrophe; the current brand.yaml for
these sub-brands sets display_name "Majiks" and lists "Majik's" as a forbidden
phrase, so the accent moves to the terminal "s" and the apostrophe is dropped.

Usage: majiks_wordmark.py <out.png> <subline> <fg-hex> <accent-hex>
"""
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = "/Users/aaronstransky/Brand-Ops/src/brand_ops/assets/fonts/SpaceGrotesk.ttf"

# 1200x360 raster of the family's 560x120 lockup viewBox (~2.14x), so the mark
# stays crisp when a card scales it down.
W, H = 1200, 360
SCALE = W / 560


def hexrgb(value):
    v = value.lstrip("#")
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))


def load(weight, size):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_name(weight)
    return f


def tracked_width(draw, text, font, tracking):
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * (len(text) - 1)


def draw_tracked(draw, x, y, text, font, tracking, fill):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def main():
    out, subline, fg_hex, accent_hex = sys.argv[1:5]
    fg, accent = hexrgb(fg_hex), hexrgb(accent_hex)

    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    # --- Primary wordmark: "Majik" + accent "s" -------------------------------
    word_font = load("Bold", round(52 * SCALE))
    word_track = 2 * SCALE
    head, tail = "Majik", "s"
    total = tracked_width(draw, head + tail, word_font, word_track)
    x = (W - total) / 2
    baseline_y = 55 * SCALE
    top = baseline_y - word_font.getmetrics()[0]
    draw_tracked(draw, x, top, head, word_font, word_track, fg)
    x += tracked_width(draw, head, word_font, word_track) + word_track
    draw_tracked(draw, x, top, tail, word_font, word_track, accent)

    # --- Subline, letter-spaced, accent --------------------------------------
    sub_font = load("Regular", round(20 * SCALE))
    sub_track = 8 * SCALE
    sub_w = tracked_width(draw, subline, sub_font, sub_track)
    sub_baseline = 90 * SCALE
    draw_tracked(
        draw,
        (W - sub_w) / 2,
        sub_baseline - sub_font.getmetrics()[0],
        subline,
        sub_font,
        sub_track,
        accent,
    )

    # --- Flanking rules at 40% accent ----------------------------------------
    rule_y = round(80 * SCALE)
    rule_h = max(2, round(1 * SCALE))
    rule_w = round(60 * SCALE)
    gap = round(24 * SCALE)
    left_end = (W - sub_w) / 2 - gap
    right_start = (W + sub_w) / 2 + gap
    faded = accent + (102,)
    rules = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rules)
    rd.rectangle([left_end - rule_w, rule_y, left_end, rule_y + rule_h], fill=faded)
    rd.rectangle([right_start, rule_y, right_start + rule_w, rule_y + rule_h], fill=faded)
    card = Image.alpha_composite(card, rules)

    card.save(out)
    print(f"wrote {out} {card.size}")


if __name__ == "__main__":
    main()
