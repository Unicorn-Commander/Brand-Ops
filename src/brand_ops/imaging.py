"""Small image post-processing helpers, kept dependency-light (Pillow only).

The one capability here is :func:`knockout_background`: turn a solid, uniform
background into transparency. Some backends (notably the LiteLLM gateway's
gpt-image-1 path) cannot return a transparent PNG even when asked, so a logo or
app icon comes back on a flat white field. For asset types that are meant to be
transparent we cut that field out here, after generation, so the saved asset has
a real alpha channel regardless of which backend produced it.

The cut is conservative by construction: it only fires when the four corners are
opaque AND nearly the same color (a genuine uniform background), and it flood
fills inward FROM the corners, so a shape of the same color in the middle of the
mark is never touched. If the background is busy or already transparent the input
is returned unchanged.
"""
from __future__ import annotations

import io

# A color the render is astronomically unlikely to contain exactly; used as the
# flood-fill paint so we can build an exact background mask from it afterwards.
_SENTINEL = (0, 255, 1)


def _uniform(rgbs: list[tuple[int, int, int]], tol: int) -> bool:
    """True when every channel varies by at most ``tol`` across the samples."""
    for channel in range(3):
        values = [c[channel] for c in rgbs]
        if max(values) - min(values) > tol:
            return False
    return True


def has_transparency(data: bytes) -> bool:
    """True when the PNG already carries at least one fully transparent pixel."""
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    if im.mode != "RGBA":
        return False
    alpha = im.getchannel("A")
    return alpha.getextrema()[0] == 0


def knockout_background(data: bytes, *, thresh: int = 50, uniform_tol: int = 24) -> bytes:
    """Cut a solid uniform background out of a PNG, returning transparent PNG bytes.

    Returns the input unchanged when there is nothing safe to do: the image is
    already transparent, or the four corners are not a single near-uniform color
    (a busy background we must not touch). ``thresh`` is the flood-fill color
    tolerance (covers anti-aliasing and compression noise at the mark's edge);
    ``uniform_tol`` is how close the corners must be to count as one background.
    """
    try:
        return _knockout_background(data, thresh=thresh, uniform_tol=uniform_tol)
    except Exception:  # noqa: BLE001 - post-processing must never break a generation
        return data


def _knockout_background(data: bytes, *, thresh: int, uniform_tol: int) -> bytes:
    from PIL import Image, ImageChops, ImageDraw

    im = Image.open(io.BytesIO(data)).convert("RGBA")
    width, height = im.size
    if width < 4 or height < 4:
        return data

    corners = [(1, 1), (width - 2, 1), (1, height - 2), (width - 2, height - 2)]
    samples = [im.getpixel(c) for c in corners]

    # Already transparent at the edges -> the backend did the right thing.
    if all(px[3] == 0 for px in samples):
        return data
    # Only proceed when the corners agree on a single background color.
    if not _uniform([px[:3] for px in samples], uniform_tol):
        return data

    rgb = im.convert("RGB")
    for corner in corners:
        ImageDraw.floodfill(rgb, corner, _SENTINEL, thresh=thresh)

    # Build the mask from "exactly the sentinel" so only the flooded background
    # region becomes transparent. Merge channels with lighter() so a pixel counts
    # as background only when ALL channels match the sentinel (a luminance
    # convert would wrongly fold near matches together).
    diff = ImageChops.difference(rgb, Image.new("RGB", im.size, _SENTINEL))
    r, g, b = diff.split()
    non_background = ImageChops.lighter(ImageChops.lighter(r, g), b)
    alpha = non_background.point(lambda v: 0 if v == 0 else 255)

    out = im.copy()
    out.putalpha(alpha)
    buffer = io.BytesIO()
    out.save(buffer, "PNG")
    return buffer.getvalue()
