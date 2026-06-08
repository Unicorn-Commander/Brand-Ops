"""BrandCriticAgent: judge an asset against a brand and suggest prompt tweaks.

Given an asset's metadata (the prompt that made it, its type and variant) and a
brand profile, the strong model judges whether the asset reads on-brand and
proposes concrete prompt edits. The judgment is advisory: it never blocks
generation, it informs a regenerate decision.

The critic does not look at pixels; it reasons over the prompt and the brand
intent. That keeps it cheap and keeps it inside the text-model lane. The copy
rules apply to its own output: no em-dashes, no marketing-speak.
"""
from __future__ import annotations

import json
from typing import Any

from .base import Agent

_SYSTEM = (
    "You are a brand reviewer. You compare an asset's generation prompt and "
    "metadata against a brand profile and judge whether the asset is on-brand. "
    "Be specific and factual. Do not use em-dashes. Do not use marketing-speak. "
    "Return only the requested JSON object."
)


class BrandCriticAgent(Agent):
    """Judge an asset against a brand profile and suggest prompt tweaks."""

    name = "brand_critic"
    description = "Judge an asset against a brand profile and suggest prompt tweaks."

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # The 2026-06-08 eval and a live run showed Qwen 3.6 produces sharp,
        # on-brand critique, so the critic defaults to the cheap local model.
        # Point AGENT_MODEL_STRONG and self.model at Claude to escalate if a
        # brand's critique quality ever slips.
        self.model = self.settings.agent_model_default

    @staticmethod
    def _critique_prompt(asset: dict[str, Any], brand_profile: dict[str, Any]) -> str:
        return (
            "Asset metadata:\n"
            f"{json.dumps(asset, indent=2, default=str)}\n\n"
            "Brand profile:\n"
            f"{json.dumps(brand_profile, indent=2, default=str)}\n\n"
            "Judge whether this asset is on-brand. Return a JSON object with "
            "these keys:\n"
            "- on_brand: true or false.\n"
            "- score: a number from 0 to 100.\n"
            "- reasons: a list of short factual observations.\n"
            "- prompt_tweaks: a list of concrete, specific edits to the "
            "generation prompt that would improve brand fit.\n"
            "Base every judgment on the brand profile's palette, fonts, voice, "
            "and style keywords. Do not invent brand attributes that are not in "
            "the profile."
        )

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Critique an asset against a brand profile.

        payload keys:
          asset          the asset metadata dict (prompt, asset_type, variant,
                         paths). A BuiltPrompt-like dict is fine.
          brand_profile  the brand profile dict to judge against (required)

        Returns the parsed critique, or a status explaining why it could not
        run (no LLM configured, or an unparseable model response).
        """
        asset = payload.get("asset")
        brand_profile = payload.get("brand_profile")
        if not isinstance(asset, dict) or not asset:
            return {"status": "error", "error": "asset metadata is required"}
        if not isinstance(brand_profile, dict) or not brand_profile:
            return {"status": "error", "error": "brand_profile is required"}

        raw = await self.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": self._critique_prompt(asset, brand_profile)},
            ],
            temperature=0.2,
            response_json=True,
        )
        if raw is None:
            return {
                "status": "skipped",
                "note": "LLM gateway not configured; critique did not run.",
            }

        critique = _parse_json_object(raw)
        if critique is None:
            return {"status": "unparsed", "raw": raw}
        return {"status": "ok", "critique": critique}


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
