"""The prompt builder injects the brand's exact hex palette, and adds a
clean-background instruction only for transparent asset types.
"""
from brand_ops.brand_loader import load_brand
from brand_ops.prompt_builder import build_prompt

_PALETTE = "Use the exact brand colors: primary #"
_CUT = "cleanly cut out to transparency"


def test_logo_prompt_injects_palette_and_transparency_clause():
    brand = load_brand("magic-unicorn")
    built = build_prompt(brand, "logo", variant="flat")
    assert _PALETTE in built.text
    assert "accent #" in built.text
    # A transparent asset gets the clean-field instruction for the knockout.
    assert _CUT in built.text


def test_og_image_prompt_has_palette_but_no_transparency_clause():
    brand = load_brand("magic-unicorn")
    built = build_prompt(brand, "og_image", topic="launch")
    assert _PALETTE in built.text
    # A non-transparent asset must not be told to leave a cut-out field.
    assert _CUT not in built.text
