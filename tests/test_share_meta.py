"""Guard the link-preview surface: index.html carries the Open Graph / Twitter
tags and the default share image ships with the web app.

Crawlers (social, iMessage, RCS, Slack, Discord) read these static tags to
unfurl a shared link into a rich card. They are easy to drop in a refactor and
their absence is silent (the link just looks bare), so this pins them in place
and pins the committed images at the right size.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
_WEB = _ROOT / "web"


def test_index_html_has_share_meta():
    html = (_WEB / "index.html").read_text(encoding="utf-8")
    for needle in (
        'property="og:type"',
        'property="og:title"',
        'property="og:description"',
        'property="og:url"',
        'property="og:image"',
        'property="og:image:width"',
        'name="twitter:card"',
        'content="summary_large_image"',
        'name="twitter:image"',
        "/og.png",
        # The absolute origin is injected at build time by vite.config.ts; the
        # placeholder must survive in source so the build can substitute it.
        "__SITE_URL__",
        'rel="icon"',
    ):
        assert needle in html, f"index.html is missing share-preview markup: {needle}"


def test_vite_substitutes_site_url():
    cfg = (_WEB / "vite.config.ts").read_text(encoding="utf-8")
    assert "__SITE_URL__" in cfg
    assert "BRAND_OPS_SITE_URL" in cfg
    assert "brand-ops.unicorncommander.ai" in cfg  # the prod default


def test_default_og_image_committed_at_og_size():
    og = _WEB / "public" / "og.png"
    assert og.is_file(), "web/public/og.png (the default link-preview image) is missing"
    im = Image.open(io.BytesIO(og.read_bytes()))
    assert im.size == (1200, 630)
    assert im.format == "PNG"


def test_favicon_committed():
    assert (_WEB / "public" / "favicon.png").is_file()
