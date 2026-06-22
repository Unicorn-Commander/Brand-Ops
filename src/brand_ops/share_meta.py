"""Per-brand social-share / link-preview metadata.

Brand-Ops generates a brand's 1200x630 Open Graph image (see ``cards.py`` /
``brand-ops card``). This builds the matching ``<meta>`` tags a brand pastes
into its OWN site's ``<head>`` so a shared link unfurls into that image on
social, iMessage, RCS, Slack, and Discord.

The preview lives on the brand's site, not in Brand-Ops, so the canonical URL
and the hosted image URL are the brand's to supply. When a brand declares a
``website`` (or the caller passes ``canonical_url`` / ``image_url``) we fill
them in; otherwise the snippet carries a clearly-marked example domain and we
report which tags still need a real value, so it is obvious what to replace.
"""
from __future__ import annotations

import html as _html
from typing import Any

from .models import Brand

# A deliberately obvious example origin, used only when the brand's real site is
# unknown so the snippet is still complete and copy-pasteable. ``.example`` is a
# reserved TLD (RFC 2606) and can never resolve, so a forgotten placeholder is
# inert rather than pointing somewhere real.
PLACEHOLDER_SITE = "https://your-domain.example"

# The Open Graph share-image size Brand-Ops composes (cards.py / og_image).
OG_WIDTH = 1200
OG_HEIGHT = 630

_DESC_LIMIT = 200


def _summarize(text: str | None, limit: int = _DESC_LIMIT) -> str:
    """Collapse whitespace and trim to ``limit`` chars at a word boundary."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."


def _title(brand: Brand) -> str:
    display = brand.brand.display_name
    tagline = brand.brand.tagline
    return f"{display} - {tagline}" if tagline else display


def _render_html(tags: list[dict[str, str]]) -> str:
    """Render the tags as ``<meta>`` lines, HTML-escaping every content value."""
    return "\n".join(
        f'<meta {t["attr"]}="{t["key"]}" content="{_html.escape(t["content"], quote=True)}" />'
        for t in tags
    )


def build_share_meta(
    brand: Brand,
    *,
    canonical_url: str | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Build a brand's Open Graph + Twitter Card link-preview metadata.

    ``canonical_url`` is the page the link points to (defaults to the brand's
    declared ``website``); ``image_url`` is where the brand hosts its share image
    (defaults to ``<site>/og.png``). Either unknown falls back to an example
    domain and is named in ``placeholders``. Returns a dict shaped for the
    ``SharePreview`` response model: ``title``, ``description``, ``url``,
    ``image_url``, ``tags`` (ordered), ``html`` (ready-to-paste), ``placeholders``.
    """
    display = brand.brand.display_name
    title = _title(brand)
    description = _summarize(
        brand.brand.description or brand.brand.tagline or display
    )

    base = (canonical_url or brand.brand.website or "").strip().rstrip("/")
    placeholders: list[str] = []

    if base:
        url = base
    else:
        url = PLACEHOLDER_SITE
        placeholders.append("og:url")

    if image_url:
        img = image_url.strip()
    elif base:
        # A working default: the brand uploads the downloaded share image to its
        # site root. Overridable via image_url if hosted elsewhere.
        img = f"{base}/og.png"
    else:
        img = f"{PLACEHOLDER_SITE}/og.png"
        placeholders.append("og:image")

    tags: list[dict[str, str]] = [
        {"attr": "property", "key": "og:type", "content": "website"},
        {"attr": "property", "key": "og:site_name", "content": display},
        {"attr": "property", "key": "og:title", "content": title},
        {"attr": "property", "key": "og:description", "content": description},
        {"attr": "property", "key": "og:url", "content": url},
        {"attr": "property", "key": "og:image", "content": img},
        {"attr": "property", "key": "og:image:width", "content": str(OG_WIDTH)},
        {"attr": "property", "key": "og:image:height", "content": str(OG_HEIGHT)},
        {"attr": "property", "key": "og:image:alt", "content": title},
        {"attr": "name", "key": "twitter:card", "content": "summary_large_image"},
        {"attr": "name", "key": "twitter:title", "content": title},
        {"attr": "name", "key": "twitter:description", "content": description},
        {"attr": "name", "key": "twitter:image", "content": img},
    ]

    return {
        "title": title,
        "description": description,
        "url": url,
        "image_url": img,
        "tags": tags,
        "html": _render_html(tags),
        "placeholders": placeholders,
    }
