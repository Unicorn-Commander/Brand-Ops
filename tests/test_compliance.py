"""Brand Compliance Loop tests.

The compliance engine is deterministic and LLM-free, so it is fully unit
testable: we synthesize small in-memory PIL images and known strings and assert
exact verdicts. No network, no model, no real image files. The REST surface is
exercised with the in-process TestClient (auth off, like test_api.py).

Pillow is required for the color-check assertions; those tests skip cleanly if
it is absent so the suite still runs without the [backends] extra.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from brand_ops.api.app import create_app
from brand_ops.brand_loader import load_brand
from brand_ops.compliance import (
    build_rules,
    build_tokens,
    check_image_colors,
    check_text,
    check_text_target,
)

try:  # the color check needs Pillow; skip those tests if it is absent
    from PIL import Image

    _HAS_PIL = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _HAS_PIL = False

needs_pil = pytest.mark.skipif(not _HAS_PIL, reason="Pillow not installed")


# ─────────────────────────── tokens + rules ─────────────────────────────────


def test_build_tokens_shape() -> None:
    """Tokens project a brand into semantic color + font tokens and a CSS block."""
    tokens = build_tokens(load_brand("magic-unicorn"))
    assert tokens["brand"] == "magic-unicorn"

    names = {t["name"] for t in tokens["color"]}
    assert "brand/primary" in names
    assert "brand/accent" in names
    # Every color token carries a hex value and an [r,g,b] triple.
    for tok in tokens["color"]:
        assert tok["value"].startswith("#")
        assert len(tok["rgb"]) == 3

    font_names = {t["name"] for t in tokens["typography"]}
    assert "font/display" in font_names and "font/body" in font_names
    # The CSS block is a copy-pasteable :root custom-property set.
    assert ":root" in tokens["css"] and "--" in tokens["css"]


def test_build_rules_shape() -> None:
    """Rules expose the allowed palette, gradient axis, and banned terms."""
    brand = load_brand("magic-unicorn")
    rules = build_rules(brand)
    assert rules["brand"] == "magic-unicorn"
    # The brand's primary/accent are in the allowed set and define the gradient.
    assert brand.visual.colors.primary in rules["allowed_colors"]
    assert rules["gradient"][:2] == [
        brand.visual.colors.primary,
        brand.visual.colors.accent,
    ]
    assert isinstance(rules["banned_terms"], list)
    assert isinstance(rules["color_tolerance"], int)


# ─────────────────────────── text checks ────────────────────────────────────

_RULES = {
    "banned_terms": ["revolutionary", "synergize"],
    "required_terms": [],
    "allowed_colors": ["#b66eff", "#00d4ff"],
    "gradient": ["#b66eff", "#00d4ff"],
    "color_tolerance": 28,
}


def _status(checks: list[dict], rule: str) -> str:
    return next(c["status"] for c in checks if c["rule"] == rule)


def test_clean_text_passes() -> None:
    checks = check_text("Innovation that ships to production.", _RULES)
    assert _status(checks, "banned_terms") == "pass"
    assert _status(checks, "em_dash") == "pass"


def test_banned_term_fails() -> None:
    checks = check_text("A revolutionary new platform.", _RULES)
    banned = next(c for c in checks if c["rule"] == "banned_terms")
    assert banned["status"] == "fail"
    assert "revolutionary" in banned["found"]


def test_em_dash_fails() -> None:
    checks = check_text("Serious systems — approachable wrapper.", _RULES)
    assert _status(checks, "em_dash") == "fail"


def test_required_term_missing_fails() -> None:
    rules = {**_RULES, "required_terms": ["sovereign"]}
    checks = check_text("A self-hosted platform.", rules)
    required = next(c for c in checks if c["rule"] == "required_terms")
    assert required["status"] == "fail"
    assert "sovereign" in required["missing"]


def test_check_text_target_combines() -> None:
    """A pure-text target combines checks into a fail with a violation line."""
    result = check_text_target("magic-unicorn", "totally revolutionary", _RULES)
    assert result["target"] == "text"
    assert result["status"] == "fail"
    assert result["score"] < 100
    assert any("banned_terms" in v for v in result["violations"])


# ─────────────────────────── color check ────────────────────────────────────


def _solid(rgb: tuple[int, int, int], size: int = 64):
    """A solid-color RGBA image."""
    return Image.new("RGBA", (size, size), rgb + (255,))


def _half(left: tuple[int, int, int], right: tuple[int, int, int], size: int = 64):
    """An image that is half ``left`` and half ``right`` (by area)."""
    img = Image.new("RGBA", (size, size), left + (255,))
    for x in range(size // 2, size):
        for y in range(size):
            img.putpixel((x, y), right + (255,))
    return img


@needs_pil
def test_on_brand_color_passes(tmp_path) -> None:
    """An image that is entirely the brand primary passes with a high score."""
    path = tmp_path / "logo.png"
    _solid((182, 110, 255)).save(path)  # #b66eff
    check = check_image_colors(path, _RULES)
    assert check["status"] == "pass"
    assert check["score"] >= 95


@needs_pil
def test_neutral_color_passes(tmp_path) -> None:
    """A near-black image passes: neutrals are always on-brand (backgrounds)."""
    path = tmp_path / "bg.png"
    _solid((10, 10, 10)).save(path)
    assert check_image_colors(path, _RULES)["status"] == "pass"


@needs_pil
def test_off_brand_color_fails(tmp_path) -> None:
    """Half pure red is well over the fail threshold and is reported."""
    path = tmp_path / "bad.png"
    _half((182, 110, 255), (255, 0, 0)).save(path)  # half brand purple, half red
    check = check_image_colors(path, _RULES)
    assert check["status"] == "fail"
    assert check["score"] < 80
    assert any(o["hex"].startswith("#") for o in check["offending"])


@needs_pil
def test_vector_skips(tmp_path) -> None:
    """An SVG cannot be pixel-read, so the color check skips, not fails."""
    path = tmp_path / "mark.svg"
    path.write_text("<svg/>")
    assert check_image_colors(path, _RULES)["status"] == "skipped"


# ─────────────────────────── REST surface ───────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    return TestClient(create_app())


def test_tokens_endpoint(client: TestClient) -> None:
    res = client.get("/api/brands/magic-unicorn/tokens")
    assert res.status_code == 200
    body = res.json()
    assert body["brand"] == "magic-unicorn"
    assert any(t["name"] == "brand/primary" for t in body["color"])


def test_rules_endpoint(client: TestClient) -> None:
    res = client.get("/api/brands/magic-unicorn/rules")
    assert res.status_code == 200
    assert "allowed_colors" in res.json()


def test_compliance_check_text_endpoint(client: TestClient) -> None:
    res = client.post(
        "/api/compliance/check",
        json={"brand": "magic-unicorn", "text": "a revolutionary breakthrough"},
    )
    assert res.status_code == 200
    body = res.json()
    # "revolutionary" is a banned term for this brand, so the copy fails.
    assert body["status"] == "fail"


def test_unknown_brand_404(client: TestClient) -> None:
    assert client.get("/api/brands/not-a-brand/rules").status_code == 404


def test_gradient_fan_color_is_on_brand() -> None:
    """A saturated color on the brand's purple->cyan hue arc is on-brand even
    when it is off the straight RGB segment; a foreign hue still fails. This is
    pure RGB/HSV math, so it does not need Pillow."""
    from brand_ops.compliance.evaluator import _hex_to_rgb, _is_on_brand

    allowed = [_hex_to_rgb(c) for c in _RULES["allowed_colors"]]
    gradient = [_hex_to_rgb(c) for c in _RULES["gradient"]]
    tol = _RULES["color_tolerance"]

    # Vivid purple/blue the straight segment misses (it sweeps desaturated mids).
    assert _is_on_brand(_hex_to_rgb("#a445ff"), allowed, gradient, tol) is True
    assert _is_on_brand(_hex_to_rgb("#4486e5"), allowed, gradient, tol) is True
    # Foreign hues at full saturation stay off-brand.
    assert _is_on_brand((255, 0, 0), allowed, gradient, tol) is False
    assert _is_on_brand((0, 200, 0), allowed, gradient, tol) is False
