"""TaggerAgent: re-classify and tag a stray asset from its metadata.

The bulk ingest mis-tagged a lot of assets: it stamped a single asset_type on
whole folders and attributed files to the wrong brand. This agent repairs that.
Given an asset's metadata (its current asset_type, the brand it is filed under,
its filename or source, and an optional short description or image url) it asks
the model to propose a corrected asset_type, a few descriptive tags, and a
judgment on whether the file really belongs to the named brand.

The agent is propose-only. It never moves a file, never rewrites a record, and
never writes a sidecar. It returns a suggestion the caller can review and apply.

The asset_type coercion is pure (coerce_asset_type) so it is fully
unit-testable and so the model can never push an unknown type past the engine:
whatever the model answers is mapped onto one of the six known asset types.

The default model is the cheap local model and the text path (filename plus
metadata) is what runs today. A vision-capable model id can be passed via the
model param to read the pixels for true image tagging; the prompt already
forwards an image_url when one is supplied, so that path needs only the model
swap. The copy rules apply to the agent's own output: no em-dashes, no
marketing-speak.
"""
from __future__ import annotations

import json
from typing import Any

from ..asset_types import ASSET_TYPES
from .base import Agent

# Free-text and near-miss labels the model (or a messy ingest record) may use,
# mapped onto the canonical asset-type key. Pure data so coercion stays a simple
# lookup. Keys are matched after lowercasing and stripping non-alphanumerics.
_TYPE_ALIASES: dict[str, str] = {
    # logo
    "logo": "logo",
    "logos": "logo",
    "logomark": "logo",
    "wordmark": "logo",
    "brandmark": "logo",
    "mark": "logo",
    # app_icon
    "appicon": "app_icon",
    "icon": "app_icon",
    "icons": "app_icon",
    "favicon": "app_icon",
    "appiconset": "app_icon",
    # og_image
    "ogimage": "og_image",
    "og": "og_image",
    "ogimg": "og_image",
    "opengraph": "og_image",
    "shareimage": "og_image",
    "share": "og_image",
    "metaimage": "og_image",
    "twittercard": "og_image",
    # podcast_cover
    "podcastcover": "podcast_cover",
    "podcast": "podcast_cover",
    "podcastart": "podcast_cover",
    "coverart": "podcast_cover",
    "albumart": "podcast_cover",
    "episodecover": "podcast_cover",
    # social_card
    "socialcard": "social_card",
    "social": "social_card",
    "socialpost": "social_card",
    "card": "social_card",
    "instagram": "social_card",
    "post": "social_card",
    "banner": "social_card",
    # avatar
    "avatar": "avatar",
    "avatars": "avatar",
    "profile": "avatar",
    "profilepic": "avatar",
    "profileimage": "avatar",
    "headshot": "avatar",
    "portrait": "avatar",
    "character": "avatar",
}

# What coerce_asset_type returns when nothing matches. social_card is the most
# generic on-brand still image, so an unknown asset lands there rather than
# being dropped or forced into a logo.
_FALLBACK_TYPE = "social_card"

_SYSTEM = (
    "You are an asset librarian. You read a single brand asset's metadata and "
    "classify it: the kind of asset it is, a few descriptive tags, and whether "
    "it belongs to the named brand. Be specific and factual. Do not use "
    "em-dashes. Do not use marketing-speak. Return only the requested JSON "
    "object."
)


class TaggerAgent(Agent):
    """Propose a corrected asset_type, tags, and a brand-match for one asset."""

    name = "tagger"
    description = "Re-classify a stray asset and propose descriptive tags from its metadata."

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Classification from filename plus metadata is well within the cheap
        # local model. Pass a vision-capable model id to read the image itself.
        self.model = self.settings.agent_model_default

    @staticmethod
    def known_types() -> list[str]:
        """The canonical asset-type keys, for menus and input schemas."""
        return sorted(ASSET_TYPES)

    @staticmethod
    def coerce_asset_type(value: Any, *, default: str = _FALLBACK_TYPE) -> str:
        """Map any label onto one of the six known asset types.

        Pure: no LLM, no network, no settings. Accepts a canonical key, a known
        alias (case and separator insensitive), or junk, and always returns a
        valid asset-type key. This is the guard that keeps a model's free-text
        answer from reaching the engine as an unknown type.
        """
        if isinstance(value, str):
            exact = value.strip().lower()
            if exact in ASSET_TYPES:
                return exact
            normalized = _normalize_label(value)
            if normalized in ASSET_TYPES:
                return normalized
            alias = _TYPE_ALIASES.get(normalized)
            if alias is not None:
                return alias
        return default if default in ASSET_TYPES else _FALLBACK_TYPE

    @staticmethod
    def _tagging_prompt(asset: dict[str, Any]) -> str:
        types = ", ".join(sorted(ASSET_TYPES))
        return (
            "Asset metadata:\n"
            f"{json.dumps(asset, indent=2, default=str)}\n\n"
            "Classify this asset. Return a JSON object with these keys:\n"
            "- asset_type: the single best fit from this exact list: "
            f"{types}.\n"
            "- tags: a list of three to six short lowercase descriptive tags "
            "(subject, style, colors, layout). No sentences.\n"
            "- belongs_to_brand: true or false, whether the file plausibly "
            "belongs to the named brand given its content and source.\n"
            "- confidence: a number from 0 to 1 for the asset_type choice.\n"
            "- notes: one short factual line, or an empty string.\n"
            "Judge from the filename, source, and any description or image. Do "
            "not invent details the metadata does not support."
        )

    @staticmethod
    def _build_messages(asset: dict[str, Any]) -> list[dict[str, Any]]:
        """Assemble the chat messages, forwarding an image_url when present.

        When the asset carries an image_url the user turn uses the multimodal
        content-parts shape so a vision-capable model reads the pixels. Without
        one it is a plain text turn, which is the path that runs today.
        """
        user_text = TaggerAgent._tagging_prompt(asset)
        image_url = asset.get("image_url")
        if isinstance(image_url, str) and image_url.strip():
            content: list[dict[str, Any]] = [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": image_url.strip()}},
            ]
            return [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": content},
            ]
        return [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_text},
        ]

    def _normalize_result(
        self, raw_result: dict[str, Any], asset: dict[str, Any]
    ) -> dict[str, Any]:
        """Shape a parsed model answer into the agent's fixed return contract.

        Coerces asset_type onto a known key, cleans the tags into short unique
        strings, and falls back to the asset's current type when the model
        omits one. Pure given its inputs.
        """
        current = asset.get("asset_type")
        suggested = raw_result.get("asset_type", current)
        asset_type = self.coerce_asset_type(
            suggested,
            default=self.coerce_asset_type(current),
        )
        return {
            "status": "ok",
            "asset_type": asset_type,
            "tags": _clean_tags(raw_result.get("tags")),
            "belongs_to_brand": _as_bool(raw_result.get("belongs_to_brand"), default=True),
            "confidence": _as_unit_float(raw_result.get("confidence")),
            "notes": _as_short_str(raw_result.get("notes")),
        }

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Propose a corrected type, tags, and brand-match for one asset.

        payload keys:
          asset  the asset metadata dict. Useful keys: asset_type (current),
                 brand, filename or source, description, image_url. Any subset
                 is accepted; richer metadata yields a better suggestion.

        Returns {status, asset_type, tags, belongs_to_brand, confidence,
        notes}. asset_type is always one of the six known types. When no LLM is
        configured the agent skips rather than guessing, but still echoes the
        coerced current type so a caller can apply at least the type repair.
        """
        asset = payload.get("asset")
        if not isinstance(asset, dict) or not asset:
            return {"status": "error", "error": "asset metadata is required"}

        raw = await self.chat(
            self._build_messages(asset),
            temperature=0.2,
            response_json=True,
        )
        if raw is None:
            return {
                "status": "skipped",
                "note": "LLM gateway not configured; classification did not run.",
                "asset_type": self.coerce_asset_type(asset.get("asset_type")),
                "tags": [],
                "belongs_to_brand": True,
                "confidence": 0.0,
            }

        parsed = _parse_json_object(raw)
        if parsed is None:
            return {"status": "unparsed", "raw": raw}
        return self._normalize_result(parsed, asset)


def _normalize_label(value: str) -> str:
    """Lowercase a label and drop every non-alphanumeric character.

    'App Icon', 'app-icon', and 'app_icon' all collapse to 'appicon' so the
    alias table needs a single entry per concept.
    """
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _clean_tags(value: Any) -> list[str]:
    """Coerce a model's tags into short, unique, lowercase strings.

    Accepts a list or a single comma-separated string. Drops blanks and
    duplicates while preserving first-seen order, and caps the count so a
    runaway answer cannot bloat the record.
    """
    if isinstance(value, str):
        items = [part for part in value.split(",")]
    elif isinstance(value, list):
        items = [str(part) for part in value]
    else:
        return []
    seen: list[str] = []
    for item in items:
        tag = item.strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
        if len(seen) >= 8:
            break
    return seen


def _as_bool(value: Any, *, default: bool) -> bool:
    """Coerce a JSON-ish truthy value to a bool, tolerating strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "yes", "y", "1"}:
            return True
        if token in {"false", "no", "n", "0"}:
            return False
    return default


def _as_unit_float(value: Any) -> float:
    """Coerce a confidence to a float clamped to the 0..1 range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _as_short_str(value: Any) -> str:
    """Coerce notes to a single trimmed line, or an empty string."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from a model response, tolerating code fences."""
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
