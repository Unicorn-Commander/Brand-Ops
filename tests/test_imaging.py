"""Background-knockout post-processing: turn a flat field into transparency
without touching the mark, and stay a no-op when it would be unsafe.
"""
import io

from PIL import Image, ImageDraw

from brand_ops.imaging import has_transparency, knockout_background


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_knockout_makes_uniform_background_transparent():
    im = Image.new("RGBA", (64, 64), (255, 255, 255, 255))  # white field
    ImageDraw.Draw(im).ellipse((20, 20, 44, 44), fill=(180, 80, 255, 255))  # purple mark
    out = knockout_background(_png(im))
    res = Image.open(io.BytesIO(out)).convert("RGBA")

    # The field is gone at the corners ...
    assert res.getpixel((1, 1))[3] == 0
    assert res.getpixel((62, 62))[3] == 0
    # ... but the centered mark survives, color intact.
    center = res.getpixel((32, 32))
    assert center[3] == 255
    assert center[0] > 100 and center[2] > 150  # still purple


def test_knockout_preserves_same_color_island_in_the_mark():
    # A white shape inside the mark, not connected to the border, must survive.
    im = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    ImageDraw.Draw(im).rectangle((16, 16, 48, 48), fill=(180, 80, 255, 255))
    ImageDraw.Draw(im).rectangle((28, 28, 36, 36), fill=(255, 255, 255, 255))  # white island
    out = knockout_background(_png(im))
    res = Image.open(io.BytesIO(out)).convert("RGBA")
    assert res.getpixel((1, 1))[3] == 0  # border field removed
    assert res.getpixel((32, 32))[3] == 255  # interior white island kept


def test_knockout_noop_when_already_transparent():
    im = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((8, 8, 24, 24), fill=(255, 0, 0, 255))
    data = _png(im)
    assert knockout_background(data) == data


def test_knockout_noop_when_background_not_uniform():
    im = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    im.putpixel((1, 1), (255, 255, 255, 255))
    im.putpixel((30, 1), (10, 10, 10, 255))
    im.putpixel((1, 30), (255, 0, 0, 255))
    im.putpixel((30, 30), (0, 255, 0, 255))
    data = _png(im)
    assert knockout_background(data) == data


def test_knockout_noop_on_invalid_bytes():
    assert knockout_background(b"not an image") == b"not an image"


def test_has_transparency():
    assert has_transparency(_png(Image.new("RGBA", (8, 8), (1, 2, 3, 255)))) is False
    assert has_transparency(_png(Image.new("RGBA", (8, 8), (1, 2, 3, 0)))) is True
