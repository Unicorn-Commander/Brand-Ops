"""The deterministic card compositor: valid PNGs at the right size, stable
output, graceful with no mark / unknown font, and the engine entry point.
"""
import io
from pathlib import Path

import pytest
from PIL import Image

from brand_ops import cards, engine
from brand_ops.brand_loader import load_brand


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def test_supported():
    assert cards.supported("og_image")
    assert cards.supported("social_card")
    assert cards.supported("launcher_tile")
    assert not cards.supported("logo")


def test_compose_og_size_and_format():
    brand = load_brand("magic-unicorn")
    data = cards.compose_card(brand, "og_image", message="Meeting intelligence you self-host")
    im = _open(data)
    assert im.size == (1200, 630)
    assert im.format == "PNG"


def test_compose_social_size():
    brand = load_brand("magic-unicorn")
    data = cards.compose_card(brand, "social_card", message="Every meeting, searchable")
    assert _open(data).size == (1080, 1080)


def test_compose_is_deterministic():
    brand = load_brand("magic-unicorn")
    a = _open(cards.compose_card(brand, "og_image", message="Same input")).tobytes()
    b = _open(cards.compose_card(brand, "og_image", message="Same input")).tobytes()
    assert a == b


def test_compose_without_mark_ok():
    brand = load_brand("magic-unicorn")
    data = cards.compose_card(
        brand, "social_card", message="No mark here", mark_path=Path("/no/such/mark.png")
    )
    assert _open(data).size == (1080, 1080)


def test_compose_unknown_asset_raises():
    brand = load_brand("magic-unicorn")
    with pytest.raises(ValueError):
        cards.compose_card(brand, "logo", message="x")


def test_generate_card_dry_run_does_not_write():
    out = engine.generate_card("magic-unicorn", "og_image", message="hi", dry_run=True)
    assert out.result is None
    assert out.built.asset_type == "og_image"
    assert out.built.backend == "compose"
    assert out.built.size == (1200, 630)


def test_generate_card_rejects_non_card_asset():
    with pytest.raises(ValueError):
        engine.generate_card("magic-unicorn", "logo", message="x", dry_run=True)


def test_render_card_returns_png_size_and_mark():
    # The pure render used to build the web app's link-preview image: PNG bytes
    # at the OG size, with the brand mark resolved and composited.
    png, mark_path = engine.render_card(
        "brand-ops", "og_image", message="Every brand, under command."
    )
    im = _open(png)
    assert im.size == (1200, 630)
    assert im.format == "PNG"
    assert mark_path is not None and Path(mark_path).is_file()


def test_render_card_social_size():
    png, _ = engine.render_card("brand-ops", "social_card", message="x")
    assert _open(png).size == (1080, 1080)


def test_render_card_rejects_non_composable():
    with pytest.raises(ValueError):
        engine.render_card("brand-ops", "logo")


def test_compose_launcher_tile_size():
    # The Ops-Center launcher tile: a 512x512 branded square with the mark, no text.
    brand = load_brand("brand-ops")
    data = cards.compose_card(brand, "launcher_tile")
    im = _open(data)
    assert im.size == (512, 512)
    assert im.format == "PNG"


def test_render_launcher_tile_resolves_mark():
    png, mark_path = engine.render_card("brand-ops", "launcher_tile")
    assert _open(png).size == (512, 512)
    assert mark_path is not None and Path(mark_path).is_file()


def test_launcher_tile_is_a_registered_asset_type():
    from brand_ops.asset_types import get_asset_spec

    spec = get_asset_spec("launcher_tile")
    assert spec.size == (512, 512)
    assert spec.transparent is False


def test_compositable_rejects_vector_and_missing():
    # Compositing is raster-only. An SVG opens fine in a browser but Pillow cannot
    # decode it, so it must never be reported as a usable mark.
    kits = Path("brand-kits")
    assert cards.compositable(kits / "magic-unicorn/imported/logo/unicorn.svg") is False
    assert cards.compositable(Path("/no/such/mark.png")) is False
    assert cards.compositable(None) is False
    assert cards.compositable(kits / "brand-ops/imported/logo/brand-ops-mark.png") is True


def test_declared_svg_mark_falls_through_to_a_raster():
    # magic-unicorn declares unicorn.svg as its mark. Before this guard the
    # resolver handed that path straight to the compositor, which silently drew
    # nothing while the caller still recorded "composited with mark unicorn.svg"
    # -- a blank card that claimed to carry the logo. The resolver must now skip
    # the undrawable mark and fall through to a raster logo.
    kits = Path("brand-kits")
    brand = load_brand("magic-unicorn", kits)
    assert brand.brand.mark.file.endswith(".svg"), "fixture: the declared mark is vector"

    resolved = engine._resolve_mark("magic-unicorn", brand, None, kits)

    assert resolved is not None
    assert resolved.suffix == ".png"
    assert cards.compositable(resolved)


def test_render_card_never_reports_an_undrawable_mark():
    # The end-to-end property: whatever render_card hands back as the mark is a
    # mark the card actually carries.
    _png, mark_path = engine.render_card("magic-unicorn", "og_image", message="x")
    assert cards.compositable(mark_path)
