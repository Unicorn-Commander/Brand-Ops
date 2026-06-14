"""BrandIntakeAgent (Iris drafting a new brand identity).

This is the agent behind real brand intake: the Director sits with the operator,
takes a brand name and whatever they can say about it, and drafts a full visual
and verbal identity the rest of Brand-Ops can author a brand.yaml from. It is the
one capability that lets a brand be CREATED through a conversation rather than
hand-edited into a file.

The flow is one LLM turn. ``draft_identity`` builds a prompt asking the gateway
(``response_json``) for a complete identity: a five-color palette, three fonts,
voice tone, style keywords, a tagline, a one-line description, and a short
rationale. When a parent brand is supplied its palette and voice are passed as
context and the model is instructed to keep the new identity clearly related to
the parent but distinct, so a sub-brand reads like family.

Every hex the model returns is validated against the same BrandColors rule the
schema enforces (``^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$``). If the model returns
junk, an incomplete palette, or there is no gateway at all, the agent does not
fail: it writes a neutral, tasteful, on-theme default identity (a dark near-black
background with a calm primary and accent) and flags that in ``notes`` so the
operator knows the identity is a starting point to refine.

The copy rules apply to Iris's own drafting exactly as everywhere else: the
tagline and description carry no em-dashes and no marketing-speak. Those are
scrubbed deterministically after the model answers, so an off-brand word the
model slips in never lands in the written brand.yaml. This module calls no
backend and reads no secret; it reaches the LLM only through the shared
``Agent.chat`` path, which returns None when there is no gateway.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import Agent
from .web_fetch import PageExtract, fetch_page_text

# The same hex rule the BrandColors validator enforces. Held here so the agent
# rejects a bad color before it ever reaches the writer, rather than letting a
# ValueError surface from model_validate downstream.
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# The five palette roles the draft must carry. primary and accent are required
# by the schema; background, foreground, and muted are optional there but the
# draft always fills them so a created brand is complete and immediately
# generatable.
_PALETTE_ROLES = ("primary", "accent", "background", "foreground", "muted")

# A neutral, tasteful, on-theme default identity. Used whole when there is no
# gateway, and role-by-role to backfill anything the model left blank or invalid.
# A dark near-black surface with a calm slate primary and a cool accent: on
# theme for this suite (dark UI) without pretending to be any specific brand.
_DEFAULT_PALETTE = {
    "primary": "#6366f1",       # indigo, a calm confident primary
    "accent": "#22d3ee",        # cyan, a cool complementary accent
    "background": "#0a0a0a",    # near-black surface
    "foreground": "#fafafa",    # off-white text
    "muted": "#71717a",         # neutral zinc for secondary text
}
_DEFAULT_TYPOGRAPHY = {
    "primary_font": "Inter",
    "display_font": "Inter",
    "mono_font": "JetBrains Mono",
}
_DEFAULT_TONE = ("clear", "direct", "grounded")
_DEFAULT_KEYWORDS = ("clean", "modern", "high contrast", "minimal")

# Words this product never ships. Mirrors the suite copy rule (and the persona
# in director.py): a drafted tagline or description that slips one is scrubbed.
# Matched case-insensitively on word boundaries so "elevated" is not caught by
# "elevate" only when it is the standalone hype word.
_HYPE_WORDS = (
    "leverage",
    "elevate",
    "unlock",
    "seamless",
    "seamlessly",
    "game-changing",
    "game-changer",
    "world-class",
    "supercharge",
    "revolutionary",
    "revolutionize",
    "synergy",
    "synergize",
    "cutting-edge",
    "best-in-class",
    "next-generation",
    "disrupt",
    "disruptive",
)

_SYSTEM = (
    "You are Iris, a brand director drafting a new brand identity. You are "
    "precise and tasteful. You design a coherent visual and verbal identity from "
    "a brand name and whatever the operator can tell you. You do not invent "
    "facts about the company; you make sound design choices and explain them "
    "plainly. ABSOLUTE copy rules: no em-dashes anywhere, and no marketing-speak "
    "or hype words (no leverage, elevate, unlock, seamless, game-changing, "
    "world-class, supercharge, revolutionary, cutting-edge). Return only the "
    "requested JSON object, nothing else."
)


class BrandIntakeAgent(Agent):
    """Draft a full brand identity for a new brand, from a name and context."""

    name = "brand_intake"
    description = (
        "Draft a full brand identity (palette, typography, voice, style "
        "keywords, tagline, description) for a new brand from its name and "
        "whatever the operator can describe, consistent with a parent when one "
        "is given."
    )

    # Intake is a creative-but-bounded drafting task: the strong model gives a
    # more coherent palette and a better-reasoned rationale than the cheap one.
    # Falls back cleanly when no gateway is configured.
    model = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model = self.settings.agent_model_strong

    # -- prompt assembly ------------------------------------------------------

    @staticmethod
    def _parent_block(parent_brand: dict[str, Any] | None) -> str:
        """Describe the parent's palette and voice as context, or empty string.

        ``parent_brand`` is a flat dict like the MCP/_brand_identity projection:
        ``{display_name, colors:{...}, voice:{tone:[...]}, style_keywords:[...]}``.
        Pure string assembly; no network.
        """
        if not isinstance(parent_brand, dict) or not parent_brand:
            return ""
        name = str(
            parent_brand.get("display_name")
            or parent_brand.get("slug")
            or "the parent brand"
        )
        colors = parent_brand.get("colors") or {}
        tone = parent_brand.get("voice") or {}
        tone_list = tone.get("tone") if isinstance(tone, dict) else None
        keywords = parent_brand.get("style_keywords") or []

        lines = [
            f"This new brand is a sub-brand of {name}. Keep its identity clearly "
            "related to the parent but distinct: a palette that reads as the same "
            "family (related hues, the same dark or light footing) without being "
            "identical, and a voice in the same spirit. Parent identity for "
            "reference:"
        ]
        if isinstance(colors, dict) and colors:
            pairs = ", ".join(
                f"{role} {colors[role]}" for role in _PALETTE_ROLES if colors.get(role)
            )
            if pairs:
                lines.append(f"- Parent palette: {pairs}")
        if isinstance(tone_list, list) and tone_list:
            lines.append(f"- Parent voice tone: {', '.join(str(t) for t in tone_list)}")
        if isinstance(keywords, list) and keywords:
            lines.append(f"- Parent style keywords: {', '.join(str(k) for k in keywords)}")
        return "\n".join(lines)

    def _draft_prompt(
        self,
        name: str,
        description: str,
        url: str,
        parent_block: str,
        mode: str,
        captured: str = "",
    ) -> str:
        """Assemble the user prompt asking the model for a full identity object.

        ``captured`` is the readable text pulled off the live site (see
        ``_page_to_context``). When it is non-empty the model drafts from the real
        page rather than guessing from the domain, so the onboard disclaimer about
        not having the site captured is dropped for that branch.
        """
        parts = [f"Brand name: {name}"]
        if description:
            parts.append(f"What the operator told me about it: {description}")
        if url:
            if captured:
                parts.append(
                    f"Their website is {url}. I captured the live page; use it as "
                    "the primary source for the brand's field, voice, and audience. "
                    "Draw the palette and keywords from what the page actually says, "
                    "not from generic assumptions."
                )
            elif mode == "onboard":
                parts.append(
                    f"Their website is {url}. Use the name and domain as hints to "
                    "the brand's field and feel. I do not have the live site "
                    "captured, so infer tastefully rather than asserting specifics."
                )
            else:
                parts.append(f"Related URL or domain: {url}")
        if captured:
            parts.append(f"Captured from the live site:\n{captured}")
        if parent_block:
            parts.append(parent_block)

        parts.append(
            "Draft a complete brand identity. Return a JSON object with exactly "
            "these keys:\n"
            "- palette: an object with hex string values for primary, accent, "
            "background, foreground, and muted. Use proper #rrggbb hex. Make the "
            "palette coherent: a strong primary, a complementary accent, a "
            "background and foreground with real contrast, and a muted neutral "
            "for secondary text.\n"
            "- typography: an object with primary_font, display_font, and "
            "mono_font. Use real, widely available font family names.\n"
            "- voice_tone: a list of three to six short adjectives for the "
            "brand's voice.\n"
            "- style_keywords: a list of four to eight short visual phrases an "
            "image model responds to (mood, texture, lighting, composition).\n"
            "- tagline: one short line, plain language, no em-dashes, no hype "
            "words.\n"
            "- description: two to four plain sentences on what the brand is and "
            "who it serves, no em-dashes, no hype words.\n"
            "- rationale: one to three plain sentences on why you chose this "
            "palette and voice."
        )
        return "\n\n".join(parts)

    # -- run / draft ----------------------------------------------------------

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Draft an identity for a new brand. Thin wrapper over ``draft_identity``.

        payload keys:
          name          the new brand's display name (required)
          description   what the operator can say about it (optional)
          url           a related URL or the live site (optional)
          parent_brand  a flat parent identity dict for a sub-brand (optional)
          mode          "describe" (default) or "onboard" (draft from a url)

        Returns ``{status, draft, notes}``. ``status`` is "ok" when the model
        drafted a usable identity and "default" when the agent fell back to the
        neutral default (no gateway or an unusable reply). It never raises for an
        LLM problem: the default path always yields a complete draft.
        """
        name = str(payload.get("name", "")).strip()
        if not name:
            return {"status": "error", "error": "name is required"}
        draft, notes = await self.draft_identity(
            name,
            description=str(payload.get("description") or "").strip(),
            url=str(payload.get("url") or "").strip(),
            parent_brand=payload.get("parent_brand"),
            mode=str(payload.get("mode") or "describe").strip().lower(),
        )
        status = "default" if any(_is_default_note(n) for n in notes) else "ok"
        return {"status": status, "draft": draft, "notes": notes}

    async def draft_identity(
        self,
        name: str,
        description: str = "",
        url: str = "",
        parent_brand: dict[str, Any] | None = None,
        mode: str = "describe",
    ) -> tuple[dict[str, Any], list[str]]:
        """Draft a full brand identity and return ``(draft, notes)``.

        ``draft`` always carries every key the writer needs: ``palette`` (five
        valid hex roles), ``typography`` (three fonts), ``voice_tone`` (list),
        ``style_keywords`` (list), ``tagline``, ``description``, and
        ``rationale``. Invalid or missing colors are backfilled from the neutral
        default and a note records that. When there is no gateway or the reply is
        unusable the whole identity is the neutral default and ``notes`` says so.

        Pure of side effects: it composes a prompt, calls the shared chat path,
        validates, and returns. It writes nothing and never raises for an LLM
        problem.
        """
        notes: list[str] = []
        parent_block = self._parent_block(parent_brand)
        mode = (mode or "describe").strip().lower()

        # Research: fetch the live site so the draft is grounded in what the page
        # actually says. Best-effort, no Brigade required; the fetcher never
        # raises and returns None when the URL is blocked, non-HTML, or fails.
        captured = ""
        if url:
            page = await fetch_page_text(url)
            if page is not None:
                captured = _page_to_context(page)

        # FUTURE BRIGADE ATTACH POINT: when Brigade is configured, augment the
        # captured context with its deep-research agent (broader web reading than
        # a single page fetch). Brigade stays a future augmentation, layered on
        # top of the page capture rather than replacing it. The shape it will
        # take, guarded behind the existing readiness check so it stays a no-op
        # until wired:
        #
        #     if url and self.brigade._research_ready():
        #         try:
        #             research = await self.brigade.research(url, depth="medium")
        #             captured = _merge_research(captured, research)
        #         except BrigadeUnavailable:
        #             pass  # best-effort: fall back to the page capture
        #
        # (BrigadeUnavailable would be imported from ..brigade.client; not done
        # here because the calls are not yet implemented.)

        if captured:
            notes.append("I read the live site and drafted from what it says.")
        elif mode == "onboard" and url:
            notes.append(
                "I could not read the live site, so I drafted from the name and "
                "the domain as hints, not the live page."
            )
        elif not description and not url:
            notes.append(
                "I drafted from the name alone. Tell me more about the brand and "
                "I will tighten the identity."
            )

        messages = [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": self._draft_prompt(
                    name, description, url, parent_block, mode, captured
                ),
            },
        ]
        raw = await self.chat(messages, temperature=0.6, response_json=True)
        if raw is None:
            # No gateway. A neutral, tasteful identity so the brand can still be
            # created and refined later.
            notes.append(_DEFAULT_NOTE_NO_GATEWAY)
            return self._default_draft(name, parent_brand), notes

        parsed = _parse_json_object(raw)
        if parsed is None:
            notes.append(_DEFAULT_NOTE_UNPARSED)
            return self._default_draft(name, parent_brand), notes

        return self._normalize_draft(parsed, name, parent_brand, notes)

    # -- normalization + default ---------------------------------------------

    def _normalize_draft(
        self,
        parsed: dict[str, Any],
        name: str,
        parent_brand: dict[str, Any] | None,
        notes: list[str],
    ) -> tuple[dict[str, Any], list[str]]:
        """Coerce a parsed model object into a complete, valid draft.

        Validates each hex, backfilling any invalid or missing color from the
        default (and noting it). Scrubs the tagline and description of em-dashes
        and hype words. Fills typography, tone, and keywords from sound defaults
        when the model omits them. ``notes`` is appended to and returned.
        """
        defaults = self._default_palette(parent_brand)
        raw_palette = parsed.get("palette")
        raw_palette = raw_palette if isinstance(raw_palette, dict) else {}

        palette: dict[str, str] = {}
        backfilled: list[str] = []
        for role in _PALETTE_ROLES:
            value = raw_palette.get(role)
            if isinstance(value, str) and _HEX.match(value.strip()):
                palette[role] = value.strip()
            else:
                palette[role] = defaults[role]
                backfilled.append(role)
        if backfilled:
            notes.append(
                "The model did not give a usable color for "
                f"{', '.join(backfilled)}; I used a tasteful default there. "
                "Refine the palette when you have it."
            )

        typography = _coerce_typography(parsed.get("typography"))
        voice_tone = _as_str_list(parsed.get("voice_tone")) or list(_DEFAULT_TONE)
        style_keywords = _as_str_list(parsed.get("style_keywords")) or list(_DEFAULT_KEYWORDS)

        tagline = _scrub(parsed.get("tagline")) or _default_tagline(name)
        description = _scrub(parsed.get("description")) or _default_description(name)
        rationale = _scrub(parsed.get("rationale")) or (
            "I chose a coherent palette with real contrast and a plain, direct "
            "voice as a sound starting point."
        )

        draft = {
            "palette": palette,
            "typography": typography,
            "voice_tone": voice_tone,
            "style_keywords": style_keywords,
            "tagline": tagline,
            "description": description,
            "rationale": rationale,
        }
        return draft, notes

    def default_draft_with_note(
        self, name: str, parent_brand: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], list[str]]:
        """The neutral default draft plus its starting-point note.

        The route's last-resort path: if ``draft_identity`` ever raises (it is
        designed not to), the route falls back here so the brand is still created
        from a complete, valid identity rather than 500ing. Returns the same
        ``(draft, notes)`` shape ``draft_identity`` returns.
        """
        return self._default_draft(name, parent_brand), [_DEFAULT_NOTE_NO_GATEWAY]

    def _default_palette(self, parent_brand: dict[str, Any] | None) -> dict[str, str]:
        """The default palette, seeded from a parent's valid colors when present.

        A sub-brand with no usable model output should still echo the parent's
        footing (its background/foreground and, failing the model, its primary)
        so the fallback for a child still reads as family rather than a generic
        neutral. Each parent color is taken only if it is a valid hex.
        """
        palette = dict(_DEFAULT_PALETTE)
        if isinstance(parent_brand, dict):
            colors = parent_brand.get("colors")
            if isinstance(colors, dict):
                for role in _PALETTE_ROLES:
                    value = colors.get(role)
                    if isinstance(value, str) and _HEX.match(value.strip()):
                        palette[role] = value.strip()
        return palette

    def _default_draft(
        self, name: str, parent_brand: dict[str, Any] | None
    ) -> dict[str, Any]:
        """A complete neutral, on-theme identity used when the model cannot help.

        Tasteful and dark by default (or the parent's footing for a sub-brand),
        with a plain tagline and description derived from the name. Everything is
        valid against the schema so the brand is created and immediately
        refinable.
        """
        return {
            "palette": self._default_palette(parent_brand),
            "typography": dict(_DEFAULT_TYPOGRAPHY),
            "voice_tone": list(_DEFAULT_TONE),
            "style_keywords": list(_DEFAULT_KEYWORDS),
            "tagline": _default_tagline(name),
            "description": _default_description(name),
            "rationale": (
                "This is a neutral starting identity: a dark, high-contrast "
                "palette and a plain, direct voice. Refine it as the brand takes "
                "shape."
            ),
        }


# -- module helpers (pure) ----------------------------------------------------

# The notes that mark a draft as the neutral default, so the route can report
# status accordingly. Kept as constants so the check is exact, not substring-
# fragile.
_DEFAULT_NOTE_NO_GATEWAY = (
    "My planner is offline, so I drafted a neutral starting identity. It is a "
    "starting point to refine, not a researched identity."
)
_DEFAULT_NOTE_UNPARSED = (
    "My planner answered in a shape I could not use, so I drafted a neutral "
    "starting identity. It is a starting point to refine."
)


def _is_default_note(note: str) -> bool:
    return note in (_DEFAULT_NOTE_NO_GATEWAY, _DEFAULT_NOTE_UNPARSED)


def _default_tagline(name: str) -> str:
    """A plain, safe tagline derived from the brand name. No hype, no em-dash."""
    return f"{name.strip()}".strip() or "A new brand"


def _default_description(name: str) -> str:
    """A plain, safe one-line description. Honest about being a starting point."""
    clean = name.strip() or "This brand"
    return (
        f"{clean} is a new brand. This description is a starting point; refine it "
        "with the brand's real purpose and audience."
    )


def _page_to_context(page: PageExtract, max_chars: int = 4000) -> str:
    """Render a fetched page into a compact context block for the draft prompt.

    Title, meta description, the h1/h2 headings, and a body-text excerpt, each on
    its own labeled line with blank lines stripped, capped to ``max_chars`` so a
    long page never dominates the prompt. Pure: takes a PageExtract, returns a
    string, no network and no side effects. Returns "" when there is nothing
    readable to show.
    """
    lines: list[str] = []
    title = (page.title or "").strip()
    if title:
        lines.append(f"Title: {title}")
    meta = (page.meta_description or "").strip()
    if meta:
        lines.append(f"Description: {meta}")
    headings = [h.strip() for h in (page.headings or []) if h and h.strip()]
    if headings:
        lines.append(f"Headings: {'; '.join(headings)}")
    body = (page.text or "").strip()
    if body:
        # Reserve room for the labeled lines above so the whole block stays under
        # max_chars; the excerpt takes whatever budget remains.
        used = sum(len(line) for line in lines) + len(lines)
        remaining = max(0, max_chars - used - len("Page text: "))
        excerpt = body[:remaining].strip() if remaining > 0 else ""
        if excerpt:
            lines.append(f"Page text: {excerpt}")
    block = "\n".join(line for line in lines if line.strip())
    return block[:max_chars].strip()


def _coerce_typography(value: Any) -> dict[str, str]:
    """Coerce a model typography object into three font fields with defaults."""
    out = dict(_DEFAULT_TYPOGRAPHY)
    if isinstance(value, dict):
        for key in ("primary_font", "display_font", "mono_font"):
            font = value.get(key)
            if isinstance(font, str) and font.strip():
                out[key] = font.strip()
    return out


def _scrub(value: Any) -> str:
    """Return a clean copy string: trimmed, em-dashes removed, hype words dropped.

    Em-dashes (and en-dashes) are replaced with a plain comma-space so a stray
    one never lands in the written brand.yaml. Hype words are removed on word
    boundaries. Returns an empty string for non-string or empty input so the
    caller substitutes a default.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    # Replace em/en dashes with a plain comma-space, then collapse the spacing.
    text = text.replace(" - ", ", ")
    text = text.replace("—", ", ").replace("–", ", ")
    for word in _HYPE_WORDS:
        text = re.sub(rf"\b{re.escape(word)}\b", "", text, flags=re.IGNORECASE)
    # Tidy the punctuation/space artifacts a removal can leave behind.
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"^[\s,;:.]+", "", text)
    return text.strip().strip(",;:").strip()


def _as_str_list(value: Any) -> list[str]:
    """Coerce a model list (or comma string) into clean, unique short strings."""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = [str(v) for v in value]
    else:
        return []
    seen: list[str] = []
    for item in items:
        token = str(item).strip()
        if token and token not in seen:
            seen.append(token)
        if len(seen) >= 12:
            break
    return seen


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from a model response, tolerating code fences.

    Mirrors the tolerant parser the other agents use: strip a ```json fence,
    then fall back to slicing the outermost braces so a little chatter around the
    object does not defeat the parse.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None
