"""The deterministic compliance engine: score pixels and copy against rules.

No LLM, no network, no randomness. Every check is a fixed algorithm, so the
same asset or text always produces the same verdict and the same score. That is
the point: this is auditable brand truth the rest of the suite can rely on, and
the seed of the product's moat — agents check their output against *this*, not a
model's opinion.

A check is a small dict with at least ``rule``, ``status``
("pass"|"warn"|"fail"|"skipped"), and ``detail``. The color check adds
``offending``; the text checks add ``found``/``missing``. ``check_asset`` and
``check_text_target`` combine checks into a :data:`ComplianceResult`-shaped dict.

Pillow is optional: ``check_image_colors`` degrades to "skipped" when Pillow is
absent or the file is a vector/unreadable, so importing this module never
requires the [backends] extra.
"""
from __future__ import annotations

import colorsys
import json
import math
from pathlib import Path
from typing import Any

from ..brand_loader import current_kits_dir

# An em-dash is off-house-style: we flag its presence in copy. The hyphen-minus
# and en-dash are fine; only U+2014 is banned.
_EM_DASH = "—"

# A significant color must hold at least this share of kept pixels to be judged;
# below it, a stray color is noise and is ignored.
_SIGNIFICANT = 0.02

# Off-brand area thresholds for the color check's overall status.
_FAIL_OVER = 0.20
_WARN_OVER = 0.07

# A color whose channel spread (max-min) is under this is "near neutral"
# (black/white/gray) and always on-brand: backgrounds and text are neutral.
_NEUTRAL_SPREAD = 24

# Gradient hue-arc acceptance. A real brand gradient (and what an image model
# renders) sweeps through more saturated colors than the straight RGB segment
# between its two endpoints, so the segment test alone wrongly flags a brand's
# own gradient. We also accept a color whose HUE sits on the arc between the two
# gradient hues, when it is genuinely saturated and bright. A foreign hue (red,
# green) still fails, and near-neutral colors are handled by the neutral rule.
_GRADIENT_HUE_MARGIN = 14.0  # degrees of slack on each end of the arc
_GRADIENT_MIN_SAT = 0.22  # below this a color is near-neutral, handled elsewhere
# Dark tints of a brand color (a brand hue laid over a near-black surface, e.g.
# a dark-mode card's gradient wash) are on brand. Keep this low so those pass;
# truly near-black pixels are low-spread and caught by the neutral rule, and a
# foreign hue is rejected regardless of how dark it is.
_GRADIENT_MIN_VAL = 0.10

# Image vector formats the pixel check cannot read; treated as skip-eligible.
_VECTOR_SUFFIXES = {".svg"}


# ─────────────────────────── text checks ────────────────────────────────────


def check_text(text: str, rules: dict) -> list[dict[str, Any]]:
    """Run the copy checks (banned terms, em-dash, required terms) on ``text``.

    Returns a list of check dicts:

    * ``banned_terms``: case-insensitive substring search for each banned term;
      ``fail`` if any are present, with the hits in ``found``.
    * ``em_dash``: ``fail`` if the text contains a U+2014 em-dash.
    * ``required_terms``: ``fail`` if any required term is absent, listing the
      gaps in ``missing``. ``pass`` (vacuously) when there are no required terms.
    """
    text = text or ""
    lowered = text.lower()

    banned = [term for term in rules.get("banned_terms", []) if term and term.lower() in lowered]
    banned_check = {
        "rule": "banned_terms",
        "status": "fail" if banned else "pass",
        "detail": (
            f"found banned term(s): {', '.join(banned)}" if banned else "no banned terms"
        ),
        "found": banned,
    }

    has_em_dash = _EM_DASH in text
    em_dash_check = {
        "rule": "em_dash",
        "status": "fail" if has_em_dash else "pass",
        "detail": "contains an em-dash (—)" if has_em_dash else "no em-dash",
    }

    required = list(rules.get("required_terms", []))
    missing = [term for term in required if term and term.lower() not in lowered]
    required_check = {
        "rule": "required_terms",
        "status": "fail" if missing else "pass",
        "detail": (
            f"missing required term(s): {', '.join(missing)}"
            if missing
            else "all required terms present"
        ),
        "missing": missing,
    }

    return [banned_check, em_dash_check, required_check]


# ─────────────────────────── color check ────────────────────────────────────


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Euclidean distance between two RGB points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _segment_distance(
    p: tuple[int, int, int],
    a: tuple[int, int, int],
    b: tuple[int, int, int],
) -> float:
    """Distance from point ``p`` to the nearest point on segment ``a``–``b``.

    Used to accept colors that fall along the brand gradient axis (the line
    between the two gradient endpoints in RGB space), not just the endpoints.
    """
    ax, ay, az = a
    bx, by, bz = b
    px, py, pz = p
    abx, aby, abz = bx - ax, by - ay, bz - az
    ab_len_sq = abx * abx + aby * aby + abz * abz
    if ab_len_sq == 0:
        # Degenerate gradient (both endpoints equal): fall back to point distance.
        return _distance(p, a)
    # Projection parameter t of p onto the line, clamped to the segment.
    t = ((px - ax) * abx + (py - ay) * aby + (pz - az) * abz) / ab_len_sq
    t = max(0.0, min(1.0, t))
    nearest = (ax + t * abx, ay + t * aby, az + t * abz)
    return math.sqrt(
        (px - nearest[0]) ** 2 + (py - nearest[1]) ** 2 + (pz - nearest[2]) ** 2
    )


def _hsv_deg(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """RGB (0-255) to HSV with hue in degrees (0-360), saturation/value in 0-1."""
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def _hue_in_arc(h: float, h0: float, h1: float, margin: float) -> bool:
    """True when hue ``h`` lies on the shorter arc between ``h0`` and ``h1``.

    The shorter arc is the gradient path between the two brand hues. When the two
    endpoints are more than 180 degrees apart the shorter arc wraps through 0.
    """
    lo, hi = sorted((h0, h1))
    if hi - lo <= 180:
        return (lo - margin) <= h <= (hi + margin)
    return h >= (hi - margin) or h <= (lo + margin)


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _is_on_brand(
    rgb: tuple[int, int, int],
    allowed: list[tuple[int, int, int]],
    gradient: list[tuple[int, int, int]],
    tolerance: int,
) -> bool:
    """Decide if a single color is on-brand by the three acceptance rules.

    On-brand if ANY of:
      (a) near-neutral (channel spread < 24): black/white/gray surfaces + text;
      (b) within ``tolerance`` of any allowed color;
      (c) within ``tolerance + 8`` of the brand gradient segment;
      (d) a saturated, bright color whose hue sits on the gradient's hue arc
          (the fan a real gradient sweeps, which the straight segment misses).
    """
    if max(rgb) - min(rgb) < _NEUTRAL_SPREAD:
        return True
    for color in allowed:
        if _distance(rgb, color) <= tolerance:
            return True
    if len(gradient) == 2:
        if _segment_distance(rgb, gradient[0], gradient[1]) <= tolerance + 8:
            return True
        hue, sat, val = _hsv_deg(rgb)
        if sat >= _GRADIENT_MIN_SAT and val >= _GRADIENT_MIN_VAL:
            h0 = _hsv_deg(gradient[0])[0]
            h1 = _hsv_deg(gradient[1])[0]
            if _hue_in_arc(hue, h0, h1, _GRADIENT_HUE_MARGIN):
                return True
    return False


def _skip(detail: str) -> dict[str, Any]:
    return {
        "rule": "color_palette",
        "status": "skipped",
        "detail": detail,
        "offending": [],
    }


def check_image_colors(image_path: Path, rules: dict) -> dict[str, Any]:
    """Score an image's color palette against the brand's allowed colors.

    Algorithm (fixed and deterministic):

    1. If the file is a vector, Pillow is missing, or the open fails, return a
       ``skipped`` color_palette check.
    2. Open as RGBA, drop pixels with alpha < 16 (transparent regions don't
       count), thumbnail to <= 96x96, then quantize to <= 64 adaptive-palette
       colors and read their frequencies.
    3. Each color's proportion is its share of kept pixels; colors with
       proportion >= 0.02 are *significant*.
    4. A significant color is on-brand if near-neutral, within tolerance of an
       allowed color, or within tolerance+8 of the gradient segment. The summed
       proportion of the off-brand significant colors is ``off_ratio``.
    5. ``status`` = fail if off_ratio > 0.20, warn if > 0.07, else pass.
       ``score`` = round((1 - off_ratio) * 100), clamped 0..100. ``offending``
       lists up to 5 off-brand colors as ``{hex, proportion}`` sorted desc.
    """
    image_path = Path(image_path)
    if image_path.suffix.lower() in _VECTOR_SUFFIXES:
        return _skip("vector image; color check not applicable")

    try:
        from PIL import Image
    except ImportError:
        return _skip("Pillow not installed; color check skipped")

    try:
        with Image.open(image_path) as raw:
            img = raw.convert("RGBA")
            img.thumbnail((96, 96))
            pixels = list(img.getdata())
    except Exception as exc:  # noqa: BLE001 - any decode failure degrades to skip
        return _skip(f"could not read image: {exc}")

    # Drop transparent pixels; they carry no brand color.
    kept = [(r, g, b) for (r, g, b, a) in pixels if a >= 16]
    total = len(kept)
    if total == 0:
        return _skip("image has no opaque pixels")

    # Quantize the opaque pixels to <= 64 colors and tally frequencies. Building
    # a fresh RGB image from the kept pixels keeps fully-transparent regions out
    # of the palette entirely.
    opaque = Image.new("RGB", (total, 1))
    opaque.putdata(kept)
    quantized = opaque.convert("P", palette=Image.ADAPTIVE, colors=64)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []  # list of (count, palette_index)

    # proportion per RGB color, mapping palette indices back to RGB triples.
    proportions: dict[tuple[int, int, int], float] = {}
    for count, index in counts:
        base = index * 3
        rgb = (palette[base], palette[base + 1], palette[base + 2])
        proportions[rgb] = proportions.get(rgb, 0.0) + count / total

    allowed = [_hex_to_rgb(c) for c in rules.get("allowed_colors", [])]
    gradient = [_hex_to_rgb(c) for c in rules.get("gradient", []) if c]
    tolerance = int(rules.get("color_tolerance", 28))

    off_ratio = 0.0
    offending: list[dict[str, Any]] = []
    for rgb, proportion in proportions.items():
        if proportion < _SIGNIFICANT:
            continue
        if not _is_on_brand(rgb, allowed, gradient, tolerance):
            off_ratio += proportion
            offending.append({"hex": _rgb_to_hex(rgb), "proportion": round(proportion, 4)})

    offending.sort(key=lambda o: o["proportion"], reverse=True)
    offending = offending[:5]

    if off_ratio > _FAIL_OVER:
        status = "fail"
    elif off_ratio > _WARN_OVER:
        status = "warn"
    else:
        status = "pass"

    score = max(0, min(100, round((1 - off_ratio) * 100)))

    return {
        "rule": "color_palette",
        "status": status,
        "detail": f"{off_ratio:.0%} of significant area is off-brand",
        "score": score,
        "offending": offending,
    }


# ─────────────────────────── combiners ──────────────────────────────────────


def _text_score(check: dict[str, Any]) -> int | None:
    """Numeric contribution of a text check: 100 on pass, 0 on fail, None skip."""
    if check["status"] == "skipped":
        return None
    return 0 if check["status"] == "fail" else 100


def _overall_status(checks: list[dict[str, Any]]) -> str:
    """fail if any check fails, else warn if any warns, else pass (skip ignored).

    If every check skipped, the overall verdict is ``skipped``.
    """
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    if any(s in ("pass",) for s in statuses):
        return "pass"
    return "skipped"


def _violation_lines(target: str, checks: list[dict[str, Any]]) -> list[str]:
    """Flatten the failing/warning checks into human-readable lines."""
    lines: list[str] = []
    for check in checks:
        if check["status"] in ("fail", "warn"):
            lines.append(f"[{check['status']}] {check['rule']}: {check['detail']}")
    return lines


def _combine(brand_slug: str, target: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble checks into a ComplianceResult-shaped dict with score + status.

    The score averages the numeric per-check scores (text checks contribute 100
    on pass / 0 on fail; the color check contributes its own 0..100 score;
    skipped checks are excluded). With no scorable check the score is 0 and the
    overall status is whatever ``_overall_status`` yields (typically skipped).
    """
    numeric: list[int] = []
    for check in checks:
        if check["rule"] == "color_palette":
            if check["status"] != "skipped" and "score" in check:
                numeric.append(int(check["score"]))
        else:
            score = _text_score(check)
            if score is not None:
                numeric.append(score)

    score = round(sum(numeric) / len(numeric)) if numeric else 0
    status = _overall_status(checks)

    # Drop the internal "score" key off the color check so the wire shape matches
    # the contract (checks carry status/detail/offending, not a per-check score).
    wire_checks: list[dict[str, Any]] = []
    for check in checks:
        wire_checks.append({k: v for k, v in check.items() if k != "score"})

    return {
        "brand": brand_slug,
        "target": target,
        "status": status,
        "score": score,
        "checks": wire_checks,
        "violations": _violation_lines(target, checks),
    }


def _resolve_image_path(
    brand_slug: str, file: str, asset_type: str, kind: str, kits_dir: Path | None
) -> Path:
    """Resolve a catalog file to its on-disk path under the kits tree."""
    root = (kits_dir or current_kits_dir()).resolve()
    return (root / brand_slug / kind / asset_type / file).resolve()


def _read_sidecar_prompt(image_path: Path) -> str | None:
    """Return the ``prompt`` recorded in the image's sidecar JSON, if any."""
    sidecar = image_path.with_suffix(image_path.suffix + ".json")
    if not sidecar.is_file():
        return None
    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return None
    prompt = data.get("prompt") if isinstance(data, dict) else None
    return prompt if isinstance(prompt, str) and prompt.strip() else None


def check_asset(
    brand_slug: str,
    file: str,
    asset_type: str,
    kind: str,
    rules: dict,
    kits_dir: Path | None = None,
) -> dict[str, Any]:
    """Score one catalog asset: its color palette plus its prompt copy, if any.

    Resolves ``brand-kits/<brand>/<kind>/<asset_type>/<file>``, runs the color
    check on it, and — when the sidecar carries a prompt — runs the text checks
    on that prompt too. Returns a combined ComplianceResult-shaped dict whose
    ``target`` is ``file:<asset_type>/<file>``.
    """
    image_path = _resolve_image_path(brand_slug, file, asset_type, kind, kits_dir)
    target = f"file:{asset_type}/{file}"

    checks: list[dict[str, Any]] = [check_image_colors(image_path, rules)]

    prompt = _read_sidecar_prompt(image_path)
    if prompt:
        checks.extend(check_text(prompt, rules))

    return _combine(brand_slug, target, checks)


def check_text_target(brand_slug: str, text: str, rules: dict) -> dict[str, Any]:
    """Score a pure piece of copy: a ComplianceResult with ``target`` "text"."""
    checks = check_text(text, rules)
    return _combine(brand_slug, "text", checks)
