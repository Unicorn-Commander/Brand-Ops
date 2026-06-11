"""Design tokens projected from a brand: the brand's visuals as machine data.

``build_tokens`` turns a loaded :class:`~brand_ops.models.Brand` into a flat,
semantic token set (``brand/primary``, ``surface/background``, ``font/display``,
…) plus a ready-to-paste block of CSS custom properties. The names are stable
semantic roles, not raw color slots, so a consumer styles against intent rather
than against a hex value that may change. Pure and deterministic: no I/O, no
network, same brand in => same tokens out.
"""
from __future__ import annotations

from ..models import Brand

# Semantic color token name <- brand color slot. Only slots present on the brand
# become tokens, so a brand without (say) a muted color simply omits text/muted.
_COLOR_TOKENS: tuple[tuple[str, str], ...] = (
    ("brand/primary", "primary"),
    ("brand/accent", "accent"),
    ("surface/background", "background"),
    ("text/foreground", "foreground"),
    ("text/muted", "muted"),
)

# Token name -> the CSS custom-property name it maps to in the :root block.
_CSS_VAR: dict[str, str] = {
    "brand/primary": "--brand-primary",
    "brand/accent": "--brand-accent",
    "surface/background": "--surface-background",
    "text/foreground": "--text-foreground",
    "text/muted": "--text-muted",
    "font/display": "--font-display",
    "font/body": "--font-body",
    "font/mono": "--font-mono",
}


def hex_to_rgb(value: str) -> list[int]:
    """Parse ``#rgb`` or ``#rrggbb`` into an ``[r, g, b]`` list of 0..255 ints.

    Shorthand three-digit hex (``#abc``) expands each nibble (``#aabbcc``), the
    same way CSS does, so a brand authored either way tokenizes identically.
    """
    h = value.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


def build_tokens(brand: Brand) -> dict:
    """Project a brand into design tokens matching the BrandTokens contract.

    Returns a dict with:

    * ``brand``: the slug.
    * ``color``: a list of ``{name, value, rgb}`` for every color the brand
      defines, in the fixed semantic order above.
    * ``typography``: ``font/display``, ``font/body`` (= the primary font), and
      ``font/mono`` as ``{name, value}`` pairs.
    * ``css``: a ``:root { … }`` block of custom properties for the colors and
      the fonts, ready to copy into a stylesheet.
    """
    colors = brand.visual.colors
    typography = brand.visual.typography

    color_tokens: list[dict] = []
    for name, slot in _COLOR_TOKENS:
        value = getattr(colors, slot, None)
        if not value:
            continue
        color_tokens.append({"name": name, "value": value, "rgb": hex_to_rgb(value)})

    typography_tokens = [
        {"name": "font/display", "value": typography.display_font},
        {"name": "font/body", "value": typography.primary_font},
        {"name": "font/mono", "value": typography.mono_font},
    ]

    css = _build_css(color_tokens, typography_tokens)

    return {
        "brand": brand.slug,
        "color": color_tokens,
        "typography": typography_tokens,
        "css": css,
    }


def _build_css(color_tokens: list[dict], typography_tokens: list[dict]) -> str:
    """Render the color and font tokens as a ``:root`` custom-property block."""
    lines = [":root {"]
    for token in color_tokens:
        var = _CSS_VAR.get(token["name"])
        if var:
            lines.append(f"  {var}: {token['value']};")
    for token in typography_tokens:
        var = _CSS_VAR.get(token["name"])
        if var:
            # Quote font family names so multi-word families stay valid CSS.
            lines.append(f'  {var}: "{token["value"]}";')
    lines.append("}")
    return "\n".join(lines)
