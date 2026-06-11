"""Template registry: data-driven, locked layouts the frontend renders on-brand.

A template is not an image and never calls a backend. It is a layout the
frontend draws on an HTML canvas using the *active brand's* design tokens: the
frontend resolves the token names here (``brand/primary``, ``text/foreground``,
…) to the brand's hex via GET /api/brands/{slug}/tokens, paints the background,
drops the brand mark, and lays each text slot in its locked font, size, color,
and position. Editing is limited to text slot *content*; the layout, fonts,
colors, and mark are locked, so every export is on-brand by construction.

Coordinates are fractions of the canvas (``x``/``y``/``w`` in 0..1) so a layout
scales to any pixel size. Colors are always brand *token names*, never raw hex,
so a brand recolor reflows every template automatically. This mirrors the
asset-type registry style in ``asset_types.py``: a frozen tuple of definitions
plus a single ``list_templates()`` projection the REST route and the MCP tool
both serve, so the human path and the agent path can never drift.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# The color token names a slot or background may reference. These are exactly the
# semantic color tokens build_tokens() emits, so a template can only ever point
# at a role the brand resolves. (text/muted included for subheads/attribution.)
COLOR_TOKENS: frozenset[str] = frozenset(
    {
        "brand/primary",
        "brand/accent",
        "surface/background",
        "text/foreground",
        "text/muted",
    }
)

# The font roles a text slot may use, the short suffix of the font/* tokens
# build_tokens() emits (font/display, font/body). "display" is the headline
# family; "body" is the primary/text family.
FONT_ROLES: frozenset[str] = frozenset({"display", "body"})


@dataclass(frozen=True)
class TemplateSlot:
    """One placeable element in a template.

    ``kind`` is ``text`` (an editable string the operator may change) or ``mark``
    (the brand logo image, drawn from the brand's mark_url; locked). For a text
    slot, ``font``/``size``/``color``/``align`` define the locked typesetting and
    ``default``/``max_len`` seed and bound the editable copy. For a mark slot
    those text fields are null. ``x``/``y`` anchor the slot and ``w`` is its max
    width, all as 0..1 fractions of the canvas.
    """

    key: str
    kind: str  # "text" | "mark"
    label: str
    x: float
    y: float
    w: float
    default: str | None = None
    max_len: int | None = None
    align: str | None = None  # "left" | "center" | "right" (text slots)
    font: str | None = None  # "display" | "body" (text slots)
    size: float | None = None  # px at full canvas scale (text slots)
    color: str | None = None  # a COLOR_TOKENS name (text slots)


@dataclass(frozen=True)
class TemplateBackground:
    """A template's background fill.

    ``kind`` is ``brand-gradient`` (the brand/primary -> brand/accent sweep; the
    frontend resolves both ends from tokens and ``color`` is null) or ``solid``
    (a flat fill whose ``color`` is a color token name like surface/background).
    """

    kind: str  # "brand-gradient" | "solid"
    color: str | None = None  # a COLOR_TOKENS name for solid; null for gradient


@dataclass(frozen=True)
class TemplateDef:
    """A locked, data-driven layout the frontend renders from brand tokens.

    ``asset_type`` maps the export onto a catalog asset type (e.g. ``og_image``,
    ``social_card``), so a saved render flows through the same library and
    compliance gate as a generated asset. ``width``/``height`` are the pixel
    canvas size; ``slots`` are positioned by 0..1 fractions so the layout scales.
    """

    id: str
    label: str
    asset_type: str
    width: int
    height: int
    background: TemplateBackground
    slots: tuple[TemplateSlot, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# The locked template set. Five good layouts, all referencing token names only.
# ---------------------------------------------------------------------------

TEMPLATES: tuple[TemplateDef, ...] = (
    # 1. OG share card — the link-preview hero. Brand gradient, big headline over
    #    a muted subhead, mark anchored top-left. 1200x630 is the OG standard.
    TemplateDef(
        id="og_hero",
        label="OG share card",
        asset_type="og_image",
        width=1200,
        height=630,
        background=TemplateBackground(kind="brand-gradient"),
        slots=(
            TemplateSlot(
                key="mark",
                kind="mark",
                label="Brand mark",
                x=0.06,
                y=0.08,
                w=0.16,
            ),
            TemplateSlot(
                key="headline",
                kind="text",
                label="Headline",
                default="Build it on brand.",
                max_len=64,
                x=0.06,
                y=0.42,
                w=0.88,
                align="left",
                font="display",
                size=84,
                color="text/foreground",
            ),
            TemplateSlot(
                key="subhead",
                kind="text",
                label="Subhead",
                default="One brand, every surface.",
                max_len=96,
                x=0.06,
                y=0.66,
                w=0.82,
                align="left",
                font="body",
                size=36,
                color="text/muted",
            ),
        ),
    ),
    # 2. Social square — the all-purpose feed post (Instagram/LinkedIn). Brand
    #    gradient, centered mark + headline + subhead. 1080x1080.
    TemplateDef(
        id="social_square",
        label="Social square",
        asset_type="social_card",
        width=1080,
        height=1080,
        background=TemplateBackground(kind="brand-gradient"),
        slots=(
            TemplateSlot(
                key="mark",
                kind="mark",
                label="Brand mark",
                x=0.40,
                y=0.14,
                w=0.20,
            ),
            TemplateSlot(
                key="headline",
                kind="text",
                label="Headline",
                default="Make something great.",
                max_len=60,
                x=0.10,
                y=0.46,
                w=0.80,
                align="center",
                font="display",
                size=76,
                color="text/foreground",
            ),
            TemplateSlot(
                key="subhead",
                kind="text",
                label="Subhead",
                default="Add your message here.",
                max_len=120,
                x=0.12,
                y=0.70,
                w=0.76,
                align="center",
                font="body",
                size=34,
                color="text/muted",
            ),
        ),
    ),
    # 3. Social story — the full-bleed vertical (Instagram/TikTok story). Brand
    #    gradient, mark up top, headline + subhead lower third. 1080x1920.
    TemplateDef(
        id="social_story",
        label="Social story",
        asset_type="social_card",
        width=1080,
        height=1920,
        background=TemplateBackground(kind="brand-gradient"),
        slots=(
            TemplateSlot(
                key="mark",
                kind="mark",
                label="Brand mark",
                x=0.40,
                y=0.10,
                w=0.20,
            ),
            TemplateSlot(
                key="headline",
                kind="text",
                label="Headline",
                default="Swipe up.",
                max_len=48,
                x=0.10,
                y=0.60,
                w=0.80,
                align="center",
                font="display",
                size=96,
                color="text/foreground",
            ),
            TemplateSlot(
                key="subhead",
                kind="text",
                label="Subhead",
                default="Tell them what's next.",
                max_len=120,
                x=0.12,
                y=0.72,
                w=0.76,
                align="center",
                font="body",
                size=40,
                color="text/muted",
            ),
        ),
    ),
    # 4. Quote card — a neutral-background pull quote with attribution. Solid
    #    surface (not the gradient) so long copy stays legible. 1080x1080.
    TemplateDef(
        id="quote_card",
        label="Quote card",
        asset_type="social_card",
        width=1080,
        height=1080,
        background=TemplateBackground(kind="solid", color="surface/background"),
        slots=(
            TemplateSlot(
                key="quote",
                kind="text",
                label="Quote",
                default="“The best brand is the one you never have to think about.”",
                max_len=200,
                x=0.10,
                y=0.30,
                w=0.80,
                align="left",
                font="display",
                size=58,
                color="text/foreground",
            ),
            TemplateSlot(
                key="attribution",
                kind="text",
                label="Attribution",
                default="— Someone worth quoting",
                max_len=80,
                x=0.10,
                y=0.74,
                w=0.80,
                align="left",
                font="body",
                size=32,
                color="brand/primary",
            ),
            TemplateSlot(
                key="mark",
                kind="mark",
                label="Brand mark",
                x=0.10,
                y=0.84,
                w=0.14,
            ),
        ),
    ),
    # 5. Feature announcement — a wide "what's new" banner. Brand gradient, an
    #    eyebrow kicker, the feature headline, and a one-line description. 1200x630.
    TemplateDef(
        id="feature_announcement",
        label="Feature announcement",
        asset_type="og_image",
        width=1200,
        height=630,
        background=TemplateBackground(kind="brand-gradient"),
        slots=(
            TemplateSlot(
                key="mark",
                kind="mark",
                label="Brand mark",
                x=0.06,
                y=0.08,
                w=0.14,
            ),
            TemplateSlot(
                key="kicker",
                kind="text",
                label="Kicker",
                default="NEW",
                max_len=24,
                x=0.06,
                y=0.34,
                w=0.50,
                align="left",
                font="body",
                size=28,
                color="brand/accent",
            ),
            TemplateSlot(
                key="headline",
                kind="text",
                label="Headline",
                default="Introducing the thing.",
                max_len=64,
                x=0.06,
                y=0.46,
                w=0.88,
                align="left",
                font="display",
                size=72,
                color="text/foreground",
            ),
            TemplateSlot(
                key="description",
                kind="text",
                label="Description",
                default="A one-line note on why it matters.",
                max_len=120,
                x=0.06,
                y=0.70,
                w=0.82,
                align="left",
                font="body",
                size=32,
                color="text/muted",
            ),
        ),
    ),
)


def list_templates() -> list[dict]:
    """Project the locked template set into plain JSON-able dicts.

    The single source both GET /api/templates and the MCP ``list_templates`` tool
    serve, so the REST surface and the agent surface never disagree on the
    template set. Each dict matches the TemplateDef wire contract: ``slots`` and
    ``background`` are nested dicts, coordinates are 0..1 fractions, and every
    color is a brand token name.
    """
    out: list[dict] = []
    for template in TEMPLATES:
        record = asdict(template)
        # ``asdict`` preserves the dataclass's tuple container for ``slots``; emit
        # a JSON-native list so the helper, the REST response (which round-trips
        # through JSON), and the MCP payload are byte-for-byte identical.
        record["slots"] = list(record["slots"])
        out.append(record)
    return out
