"""BrandResearchAgent: turn a company plus domain into a structured profile.

The flow is two steps. First it delegates web research to Brigade's
deep-research agent to gather public context about the company. Then it asks the
strong model to synthesize that context into a structured brand profile and,
optionally, to shape that profile into a brand.yaml dict that the rest of
Brand-Ops can author from.

The synthesis prompt is held to the suite copy rules: no em-dashes, no
marketing-speak. The agent never invents a result when no LLM is configured; it
returns a clear status so the caller knows research did not run.
"""
from __future__ import annotations

import json
from typing import Any

from ..brigade import BrigadeUnavailable
from .base import Agent

# The fields the model is asked to return. Kept here so the prompt, the result
# shape, and the brand.yaml mapping all read from one place.
PROFILE_FIELDS = (
    "display_name",
    "tagline",
    "palette",
    "fonts",
    "voice",
    "style_keywords",
)

_SYSTEM = (
    "You are a brand analyst. You read public web context about a company and "
    "produce a precise, factual brand profile. Do not use em-dashes. Do not use "
    "marketing-speak or hype words. Return only the requested JSON object."
)


class BrandResearchAgent(Agent):
    """Research a company and synthesize a structured brand profile."""

    name = "brand_research"
    description = "Research a company and synthesize a structured brand profile."

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Research and critique escalate to the strong model.
        self.model = self.settings.agent_model_strong

    def _synthesis_prompt(self, company: str, domain: str, context: str) -> str:
        fields = ", ".join(PROFILE_FIELDS)
        return (
            f"Company: {company}\n"
            f"Domain: {domain}\n\n"
            f"Web research context:\n{context}\n\n"
            "From the context above, produce a JSON object with exactly these "
            f"keys: {fields}.\n"
            "- display_name: the brand's public name as a string.\n"
            "- tagline: one short factual line, or an empty string if none is clear.\n"
            "- palette: an object with hex strings primary and accent, and "
            "optionally background and foreground.\n"
            "- fonts: an object with primary_font and display_font.\n"
            "- voice: an object with tone (a list of short adjectives) and "
            "forbidden_phrases (a list).\n"
            "- style_keywords: a list of short visual descriptors.\n"
            "Use only what the context supports. Where the context is silent, "
            "use a neutral, defensible value rather than inventing specifics."
        )

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Research {company, domain} and synthesize a brand profile.

        Returns a dict with status, the raw research context when available, the
        synthesized profile when the LLM ran, and a brand_yaml dict shaped for
        authoring.
        """
        company = str(payload.get("company", "")).strip()
        domain = str(payload.get("domain", "")).strip()
        if not company:
            return {"status": "error", "error": "company is required"}

        depth = str(payload.get("depth", "medium"))
        result: dict[str, Any] = {"status": "ok", "company": company, "domain": domain}

        # Step 1: delegate web research to Brigade. It is optional: if Brigade is
        # not configured the agent still synthesizes from the company and domain.
        context = ""
        topic = f"Brand identity research for {company} ({domain})" if domain else (
            f"Brand identity research for {company}"
        )
        try:
            research = await self.brigade.research(topic, depth=depth)
            context = _research_to_text(research)
            result["research"] = research
        except BrigadeUnavailable as exc:
            result["research_status"] = str(exc)

        # Step 2: synthesize with the strong model. No LLM means no profile.
        raw = await self.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": self._synthesis_prompt(company, domain, context)},
            ],
            response_json=True,
        )
        if raw is None:
            result["status"] = "research_only"
            result["note"] = "LLM gateway not configured; returning research context only."
            return result

        profile = _parse_profile(raw)
        if profile is None:
            result["status"] = "unparsed"
            result["raw"] = raw
            return result

        result["profile"] = profile
        result["brand_yaml"] = self.draft_brand_yaml(domain or company, profile)
        return result

    @staticmethod
    def draft_brand_yaml(slug_source: str, profile: dict[str, Any]) -> dict[str, Any]:
        """Shape a synthesized profile into a brand.yaml dict.

        Pure and deterministic. Maps the profile's flat-ish shape onto the
        brand.yaml schema (brand / visual / voice) without touching the network.
        """
        slug = _slugify(slug_source)
        palette = profile.get("palette") or {}
        fonts = profile.get("fonts") or {}
        voice = profile.get("voice") or {}

        visual_colors: dict[str, Any] = {}
        for key in ("primary", "accent", "background", "foreground", "muted"):
            value = palette.get(key)
            if isinstance(value, str) and value:
                visual_colors[key] = value

        typography: dict[str, Any] = {}
        for src, dst in (("primary_font", "primary_font"), ("display_font", "display_font")):
            value = fonts.get(src)
            if isinstance(value, str) and value:
                typography[dst] = value

        return {
            "brand": {
                "slug": slug,
                "display_name": profile.get("display_name") or slug_source,
                "tagline": profile.get("tagline") or None,
            },
            "visual": {
                "colors": visual_colors,
                "typography": typography,
                "style_keywords": _as_str_list(profile.get("style_keywords")),
            },
            "voice": {
                "tone": _as_str_list(voice.get("tone")),
                "forbidden_phrases": _as_str_list(voice.get("forbidden_phrases")),
            },
        }


def _research_to_text(research: dict[str, Any]) -> str:
    """Flatten a Brigade research payload into prompt-ready text.

    Brigade may return a summary string, a list of findings, or a report field.
    This stays permissive so a schema tweak on the hub does not break synthesis.
    """
    for key in ("summary", "report", "content", "text"):
        value = research.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    findings = research.get("findings")
    if isinstance(findings, list):
        parts = [str(f) for f in findings if f]
        if parts:
            return "\n".join(parts)
    return json.dumps(research)[:4000]


def _parse_profile(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object out of an LLM response, tolerating code fences."""
    text = raw.strip()
    if text.startswith("```"):
        # Drop a leading ```json or ``` fence and the trailing fence.
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


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _slugify(text: str) -> str:
    """A small, dependency-free slug for the brand key."""
    cleaned = []
    for ch in text.strip().lower():
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {" ", "-", "_", "."}:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "brand"
