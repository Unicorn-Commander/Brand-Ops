"""GenerationPlanner: turn a free-text ask into a valid GenerateRequest.

The Studio has a free-text box ("make an OG image for the investor update,
dark"). This module interprets that line into a concrete generation request: a
brand slug, an asset type, and optional variant/topic/platform/backend, plus a
short plain-language rationale the operator can read.

The flow mirrors the Director's: one LLM turn that asks the gateway for a strict
JSON object, parsed and then VALIDATED against what the backend actually knows
(the real brand slugs, the real asset-type keys, and the variants each asset type
offers). Validation is the load-bearing half: a model that picks a brand or asset
that does not exist, or a variant the asset does not offer, is corrected here so
the endpoint never returns a GenerateRequest that would 422 downstream.

When the gateway is absent, the model reply will not parse, or the model's pick
fails validation, the planner falls back to a deterministic server-side keyword
heuristic ported from the frontend stub it replaces. The result is the same
either way: a sensible, fully valid GenerateRequest. This module calls no backend
and reads no secret; it reaches the LLM only through the shared Agent.chat path,
which returns None when there is no gateway.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..asset_types import ASSET_TYPES
from ..prompt_builder import DEFAULT_PROMPTS
from .base import Agent

# How the heuristic recognizes an asset type from the request text, in priority
# order: the first asset type whose key is known and whose phrases match wins.
# Ported from the frontend stub (web/src/pages/studio/lib/agent.ts ASSET_HINTS)
# so the offline path behaves the same as the client did. Keep all copy free of
# em-dashes and marketing-speak.
_ASSET_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "og_image",
        ("og image", "og-image", "open graph", "social preview", "link preview", "share image"),
    ),
    ("podcast_cover", ("podcast", "episode cover", "cover art", "show art")),
    ("app_icon", ("app icon", "ios icon", "android icon", "launcher icon")),
    (
        "social_card",
        ("social card", "twitter card", "x card", "linkedin card", "post image", "social post"),
    ),
    ("avatar", ("avatar", "profile picture", "profile pic", "headshot", "pfp")),
    ("logo", ("logo", "wordmark", "logomark", "brandmark")),
)

# Generic variant keywords the heuristic recognizes. A generic only applies when
# the asset type actually offers it (or offers no variant list at all). Mirrors
# the frontend stub's VARIANT_HINTS.
_VARIANT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dark", ("dark", "night", "black background", "on black")),
    ("light", ("light", "white background", "on white", "bright")),
    ("transparent", ("transparent", "no background", "cutout", "knockout")),
    ("mono", ("mono", "monochrome", "single color", "one color", "grayscale")),
)

# "for/about/on <topic>" extraction, then trim a trailing asset noun. Ported from
# the frontend stub's inferTopic so a topic reads the same as the client found.
_TOPIC_RE = re.compile(
    r"(?:for|about|on)\s+(?:the\s+|our\s+|a\s+|an\s+)?([a-z0-9][a-z0-9 \-]{2,40})"
)
_TOPIC_TRAIL_RE = re.compile(r"\b(image|icon|logo|cover|card|avatar|graphic|asset)\b.*$")


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace, so phrase matching is stable."""
    return re.sub(r"\s+", " ", text).strip().lower()


def _asset_variants(asset: str) -> list[str]:
    """The variants an asset type offers, from the default prompt template.

    The same source build_prompt validates a variant against: a brand can
    override a template, but the planner works without a loaded brand, so it reads
    the shared DEFAULT_PROMPTS. An asset with no template (none ship without one)
    yields an empty list, which the validator reads as "accept any variant".
    """
    template = DEFAULT_PROMPTS.get(asset)
    return list(template.variants) if template and template.variants else []


def _first_asset_key() -> str | None:
    """The first registered asset-type key, the last-resort asset default."""
    return next(iter(ASSET_TYPES), None)


def _resolve_brand(
    candidate: str | None, brands: list[dict[str, str]], fallback: str | None
) -> str | None:
    """Coerce a brand pick to a real slug: the candidate, the fallback, else first.

    ``candidate`` is the model's (or the heuristic's) pick; it is accepted only
    when it is one of the operator's real slugs. Otherwise the request's ``brand``
    control is used when it too is a real slug, and failing that the first brand.
    Returns None only when the operator has no brands at all.
    """
    slugs = {b["slug"] for b in brands}
    if candidate and candidate in slugs:
        return candidate
    if fallback and fallback in slugs:
        return fallback
    return brands[0]["slug"] if brands else None


def _resolve_asset(candidate: str | None, fallback: str | None) -> str | None:
    """Coerce an asset pick to a real asset-type key: candidate, fallback, first."""
    if candidate and candidate in ASSET_TYPES:
        return candidate
    if fallback and fallback in ASSET_TYPES:
        return fallback
    return _first_asset_key()


def _resolve_variant(candidate: str | None, asset: str) -> str | None:
    """Keep a variant only when the asset offers it (or offers no variant list).

    A variant the asset type does not offer would make build_prompt raise, so it
    is dropped here rather than returned in an invalid request. When the asset
    declares no variants, any non-empty variant is passed through.
    """
    if not candidate:
        return None
    offered = _asset_variants(asset)
    if not offered or candidate in offered:
        return candidate
    return None


def _brand_display(brands: list[dict[str, str]], slug: str | None) -> str:
    """The display name for a slug, falling back to the slug itself."""
    for b in brands:
        if b["slug"] == slug:
            return b.get("display_name") or slug or ""
    return slug or ""


def _asset_label(asset: str | None) -> str:
    """The human label for an asset-type key, falling back to the key."""
    spec = ASSET_TYPES.get(asset) if asset else None
    return spec.label if spec else (asset or "")


def keyword_plan(
    text: str,
    brands: list[dict[str, str]],
    *,
    brand: str | None = None,
    asset: str | None = None,
    variant: str | None = None,
    topic: str | None = None,
    platform: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    """Plan a request from keywords alone, with no LLM. Always returns a valid plan.

    The deterministic fallback (and the offline path) for the planner: it reads
    the free text plus the current control values and produces a result dict in
    the PlanGenerationReply shape (``request`` a valid GenerateRequest, plus
    ``rationale`` and ``assumed`` lines). Ported from the frontend stub it
    replaces, so the offline backend path behaves as the client did.

    The brand resolves to a named brand, then the ``brand`` control, then the
    first brand; the asset to a matched type, then the ``asset`` control, then the
    first registered type. Variant, topic, platform, and backend are inferred from
    the text where present and otherwise taken from the controls.
    """
    norm = _normalize(text)
    rationale: list[str] = []
    assumed: list[str] = []

    # Brand: a named brand in the text wins; else the control; else the first.
    named_brand = _match_brand(norm, brands)
    brand_slug = _resolve_brand(named_brand, brands, brand)
    if brand_slug is not None:
        name = _brand_display(brands, brand_slug)
        if brand_slug == named_brand:
            rationale.append(f"Brand: {name} (named in the request).")
        else:
            rationale.append(f"Brand: {name} (current selection).")

    # Asset: a matched type wins; else the control; else the first registered.
    matched_asset = _match_asset(norm)
    asset_key = _resolve_asset(matched_asset, asset)
    if asset_key is not None:
        label = _asset_label(asset_key)
        if asset_key == matched_asset:
            rationale.append(f"Asset: {label} (matched the request).")
        elif asset_key == asset:
            rationale.append(f"Asset: {label} (current selection).")
        else:
            rationale.append(f"Asset: {label} (default).")

    # Variant: a generic keyword the asset offers, else the control if the asset
    # offers it. Anything the asset does not offer is dropped.
    matched_variant = _match_variant(norm, asset_key)
    variant_value = _resolve_variant(matched_variant or variant, asset_key or "")
    if variant_value is not None:
        if variant_value == matched_variant:
            rationale.append(f"Variant: {variant_value} (matched the request).")
        else:
            rationale.append(f"Variant: {variant_value} (current selection).")

    # Topic: a "for/about/on <topic>" phrase, else the topic control.
    matched_topic = _match_topic(norm)
    topic_value = matched_topic or topic
    if topic_value is not None:
        if matched_topic:
            rationale.append(f'Topic: "{topic_value}" (from the request).')
        else:
            rationale.append(f'Topic: "{topic_value}" (from the topic field).')

    # Backend: an explicit control is kept; otherwise the asset's default is noted
    # as an assumption (the request leaves backend null so the engine applies it).
    if backend:
        rationale.append(f"Backend: {backend} (current selection).")
    elif asset_key is not None:
        spec = ASSET_TYPES.get(asset_key)
        if spec is not None:
            assumed.append(f"backend defaults to {spec.default_backend} for this asset type")

    request = {
        "brand": brand_slug,
        "asset": asset_key,
        "variant": variant_value,
        "topic": topic_value,
        "platform": platform,
        "backend": backend,
    }
    return {"request": request, "rationale": rationale, "assumed": assumed}


def _match_brand(norm: str, brands: list[dict[str, str]]) -> str | None:
    """Return the slug of a brand whose name or slug appears in the text, or None."""
    for b in brands:
        name = _normalize(b.get("display_name", ""))
        slug = _normalize(b.get("slug", "")).replace("-", " ")
        if (len(name) > 2 and name in norm) or (len(slug) > 2 and slug in norm):
            return b["slug"]
    return None


def _match_asset(norm: str) -> str | None:
    """Return the first known asset-type key whose phrases match the text, or None."""
    for key, phrases in _ASSET_HINTS:
        if key not in ASSET_TYPES:
            continue
        if any(phrase in norm for phrase in phrases):
            return key
    return None


def _match_variant(norm: str, asset: str | None) -> str | None:
    """Return a generic variant keyword the asset offers, matched in the text."""
    offered = _asset_variants(asset) if asset else []
    for value, phrases in _VARIANT_HINTS:
        if any(phrase in norm for phrase in phrases):
            if not offered or value in offered:
                return value
    return None


def _match_topic(norm: str) -> str | None:
    """Pull a short topic out of the text ("for the investor update"), or None."""
    match = _TOPIC_RE.search(norm)
    if not match:
        return None
    topic = _TOPIC_TRAIL_RE.sub("", match.group(1))
    topic = topic.replace(",", "").replace(".", "").strip()
    return topic if len(topic) >= 3 else None


# The planner's instruction to the model. Persona-free and terse: this is a
# classifier, not a conversation, so it stays cheap and fast. The asset list and
# per-asset variants are injected at call time from what the backend really knows.
_PLANNER_CONTRACT = (
    "You turn a short request for a brand asset into a generation plan. Reply "
    "with a single JSON object and nothing else: no prose, no code fence. The "
    "object has exactly these keys:\n"
    "- brand: the slug of the brand this is for. Use one of the brand slugs "
    "listed below. Default to the active brand slug when the request does not "
    "name another. Null only if there are no brands.\n"
    "- asset: the asset-type key to generate. Use one of the asset keys listed "
    "below, matching the request.\n"
    "- variant: a variant for that asset type if the request implies one, chosen "
    "from that asset's offered variants listed below, else null.\n"
    "- topic: a short subject the asset is about if the request gives one (e.g. "
    "the investor update), else null.\n"
    "- platform: a target platform if the request names one (e.g. instagram), "
    "else null.\n"
    "- rationale: a list of short plain-language strings, one per field you "
    "decided, e.g. 'Asset: OG share image (matched the request).' Plain "
    "language, no marketing words, no em-dashes.\n"
    "Decide only from the request text. Do not invent a brand, asset, or variant "
    "that is not in the lists below."
)


class GenerationPlanner(Agent):
    """Interpret a free-text generation ask into a validated GenerateRequest."""

    name = "generation_planner"
    description = (
        "Turns a free-text Studio request into a concrete, valid generation "
        "request: a brand, an asset type, and optional variant, topic, platform, "
        "and backend, with a short rationale."
    )

    # Light classification work, so the default (Qwen-first) model is right; a
    # deployment can point self.model at a stronger model for richer parsing.
    model = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model = self.settings.agent_model_default

    # -- prompt assembly ------------------------------------------------------

    @staticmethod
    def _brands_block(brands: list[dict[str, str]]) -> str:
        """Render the operator's brands as a slug + display-name list."""
        if not brands:
            return "The operator has no brands set up yet."
        lines = [
            f"- {b.get('display_name') or b['slug']} (slug: {b['slug']})"
            for b in brands
            if b.get("slug")
        ]
        return "Brands:\n" + "\n".join(lines) if lines else "The operator has no brands."

    @staticmethod
    def _assets_block() -> str:
        """Render the asset types as key, label, default backend, and variants."""
        lines = []
        for spec in ASSET_TYPES.values():
            variants = _asset_variants(spec.key)
            variant_note = f"; variants: {', '.join(variants)}" if variants else ""
            lines.append(
                f"- {spec.key} ({spec.label}; default backend {spec.default_backend}"
                f"{variant_note})"
            )
        return "Asset types:\n" + "\n".join(lines)

    @staticmethod
    def _active_block(brand: str | None) -> str:
        """State the active brand slug (the brand to default to), or its absence."""
        if brand:
            return f"Active brand slug: {brand}. Default to it unless the request names another."
        return "No brand is active. Pick the brand the request names, or the first brand."

    def _system_prompt(self, brand: str | None, brands: list[dict[str, str]]) -> str:
        """Assemble the planner system prompt: contract, brands, assets, active."""
        return (
            f"{_PLANNER_CONTRACT}\n\n"
            f"{self._brands_block(brands)}\n\n"
            f"{self._assets_block()}\n\n"
            f"{self._active_block(brand)}"
        )

    # -- run ------------------------------------------------------------------

    async def plan(
        self,
        text: str,
        brands: list[dict[str, str]],
        *,
        brand: str | None = None,
        asset: str | None = None,
        variant: str | None = None,
        topic: str | None = None,
        platform: str | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        """Plan a generation request from the text, the brands, and the controls.

        Returns a dict in the PlanGenerationReply shape. The LLM is asked for a
        strict JSON pick; its brand/asset/variant are validated against the real
        brand slugs and asset-type keys. On no gateway, an unparseable reply, or a
        pick that fails validation, it falls back to the keyword heuristic, so the
        result is always a valid GenerateRequest. Never raises.
        """
        controls = {
            "brand": brand,
            "asset": asset,
            "variant": variant,
            "topic": topic,
            "platform": platform,
            "backend": backend,
        }

        # An empty request has no text to interpret; go straight to the heuristic,
        # which falls back to the controls (or the first brand and asset type).
        if not text or not text.strip():
            return keyword_plan(text or "", brands, **controls)

        try:
            raw = await self._ask_model(text, brand, brands)
        except Exception:  # noqa: BLE001 - any LLM/transport error falls back to the heuristic
            raw = None
        if raw is None:
            return keyword_plan(text, brands, **controls)

        parsed = _parse_json_object(raw)
        if parsed is None:
            return keyword_plan(text, brands, **controls)

        plan = self._validate(parsed, text, brands, controls)
        # The validator returns None when the model gave nothing usable for the
        # required fields (no resolvable brand or asset); fall back rather than
        # return an incomplete request.
        if plan is None:
            return keyword_plan(text, brands, **controls)
        return plan

    async def _ask_model(
        self, text: str, brand: str | None, brands: list[dict[str, str]]
    ) -> str | None:
        """One LLM turn asking for the strict JSON plan. Returns the raw text or None."""
        system = self._system_prompt(brand, brands)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ]
        # Low temperature and a JSON response: this is a deterministic-ish
        # classification, not creative writing, and we want strict JSON back.
        return await self.chat(messages, temperature=0.2, response_json=True)

    def _validate(
        self,
        parsed: dict[str, Any],
        text: str,
        brands: list[dict[str, str]],
        controls: dict[str, str | None],
    ) -> dict[str, Any] | None:
        """Coerce a parsed model object into a valid PlanGenerationReply dict.

        The brand and asset are forced to real values (the model's pick when it is
        real, else the control, else the first); a variant the asset does not offer
        is dropped; topic/platform fall back to the controls; backend is the
        control only (the model is not asked to pick a backend). Returns None when
        no brand or no asset can be resolved (an empty backend), so the caller
        falls back to the heuristic.
        """
        brand_slug = _resolve_brand(_clean_str(parsed.get("brand")), brands, controls["brand"])
        asset_key = _resolve_asset(_clean_str(parsed.get("asset")), controls["asset"])
        if brand_slug is None or asset_key is None:
            return None

        variant_value = _resolve_variant(
            _clean_str(parsed.get("variant")) or controls["variant"], asset_key
        )
        topic_value = _clean_str(parsed.get("topic")) or controls["topic"]
        platform_value = _clean_str(parsed.get("platform")) or controls["platform"]
        backend_value = controls["backend"]

        rationale = self._rationale(parsed, brands, brand_slug, asset_key)
        assumed: list[str] = []
        if not backend_value:
            spec = ASSET_TYPES.get(asset_key)
            if spec is not None:
                assumed.append(f"backend defaults to {spec.default_backend} for this asset type")

        request = {
            "brand": brand_slug,
            "asset": asset_key,
            "variant": variant_value,
            "topic": topic_value,
            "platform": platform_value,
            "backend": backend_value,
        }
        return {"request": request, "rationale": rationale, "assumed": assumed}

    @staticmethod
    def _rationale(
        parsed: dict[str, Any],
        brands: list[dict[str, str]],
        brand_slug: str,
        asset_key: str,
    ) -> list[str]:
        """Use the model's rationale lines when sane, else synthesize honest ones.

        The model is asked for a ``rationale`` list; clean strings from it are
        kept. When it gave none, fall back to two plain lines naming the resolved
        brand and asset so the operator always sees why the plan looks as it does.
        """
        raw = parsed.get("rationale")
        lines: list[str] = []
        if isinstance(raw, list):
            lines = [s.strip() for s in raw if isinstance(s, str) and s.strip()]
        if lines:
            return lines
        return [
            f"Brand: {_brand_display(brands, brand_slug)} (chosen for the request).",
            f"Asset: {_asset_label(asset_key)} (chosen for the request).",
        ]

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Agent entry point: plan from a payload dict (the Agent contract).

        payload keys mirror PlanGenerationRequest: ``text`` plus the optional
        control values and ``brands`` (the operator's {slug, display_name} list).
        Returns the PlanGenerationReply dict.
        """
        text = str(payload.get("text", ""))
        brands = payload.get("brands") or []
        if not isinstance(brands, list):
            brands = []
        return await self.plan(
            text,
            brands,
            brand=_clean_str(payload.get("brand")),
            asset=_clean_str(payload.get("asset")),
            variant=_clean_str(payload.get("variant")),
            topic=_clean_str(payload.get("topic")),
            platform=_clean_str(payload.get("platform")),
            backend=_clean_str(payload.get("backend")),
        )


def _clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, or None. Treats null-ish text as None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from a model response, tolerating code fences.

    Mirrors the tolerant parser the Director uses: strip a ```json fence, then
    fall back to slicing the outermost braces so a little chatter around the
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
