"""RefineAgent: apply a critic's prompt tweaks to a generation prompt.

The BrandCriticAgent judges an asset and returns prompt_tweaks: concrete edits
that would improve brand fit. This agent closes that loop by folding those
tweaks back into the prompt, producing a refined prompt the engine can run next.

The merge is deterministic by default. merge_tweaks is pure: it takes the base
prompt and the list of tweaks and appends each tweak as an explicit directive,
deduplicating and skipping tweaks already reflected in the prompt. That path
needs no LLM and is fully unit-testable, so refinement works in CI and offline.

The model is used only as a smoothing layer: when a gateway is configured the
agent asks the cheap local model to rewrite the deterministic merge into one
natural prompt. If the model is absent or its answer is unusable the
deterministic merge is returned as-is, so the agent always produces a refined
prompt.

This agent never calls the image engine. It returns the refined prompt and the
list of tweaks it applied; a caller decides whether to regenerate. The copy
rules apply to its own output: no em-dashes, no marketing-speak.
"""
from __future__ import annotations

from typing import Any

from .base import Agent

_SYSTEM = (
    "You rewrite image-generation prompts. You are given a base prompt and a "
    "list of edits to apply. Produce one clear prompt that incorporates every "
    "edit while preserving the base subject and intent. Do not add directives "
    "that were not asked for. Do not use em-dashes. Do not use marketing-speak. "
    "Return only the rewritten prompt as plain text, with no preamble."
)


class RefineAgent(Agent):
    """Apply a critic's prompt tweaks to a prompt and return the refined prompt."""

    name = "refine"
    description = "Apply a critic's prompt tweaks to a generation prompt and return the result."

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Smoothing the merge into natural language is light work for the cheap
        # local model. The deterministic merge underneath needs no model at all.
        self.model = self.settings.agent_model_default

    @staticmethod
    def extract_tweaks(critique: Any) -> list[str]:
        """Pull the prompt_tweaks list out of a critic result, tolerantly.

        Pure: no LLM, no network. Accepts the full BrandCriticAgent return
        ({status, critique: {prompt_tweaks: [...]}}), a bare critique dict, or a
        plain list of tweaks. Returns a clean list of non-empty tweak strings in
        order, dropping blanks and duplicates.
        """
        tweaks: Any = None
        if isinstance(critique, list):
            tweaks = critique
        elif isinstance(critique, dict):
            inner = critique.get("critique")
            source = inner if isinstance(inner, dict) else critique
            tweaks = source.get("prompt_tweaks")
        if not isinstance(tweaks, list):
            return []
        cleaned: list[str] = []
        for tweak in tweaks:
            text = str(tweak).strip()
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @staticmethod
    def merge_tweaks(prompt: str, tweaks: list[str]) -> tuple[str, list[str]]:
        """Fold tweaks into a prompt deterministically.

        Pure: no LLM, no network. Appends each tweak the prompt does not already
        reflect as an explicit directive after a single separator, preserving
        order. Returns the merged prompt and the list of tweaks actually applied
        (a tweak whose text already appears in the prompt is skipped). With no
        applicable tweaks the prompt is returned unchanged.
        """
        base = (prompt or "").strip()
        existing = base.lower()
        applied: list[str] = []
        for tweak in tweaks:
            text = tweak.strip()
            if not text:
                continue
            if text.lower() in existing:
                # Already satisfied by the base prompt; do not restate it.
                continue
            applied.append(text)
        if not applied:
            return base, []
        directives = ". ".join(_strip_trailing_period(t) for t in applied)
        if base:
            merged = f"{_strip_trailing_period(base)}. {directives}."
        else:
            merged = f"{directives}."
        return merged, applied

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Refine an asset's prompt by applying a critic's tweaks.

        payload keys:
          asset     the asset metadata dict; its prompt is the base to refine.
                    A plain {prompt: "..."} dict is fine. A top-level prompt key
                    on the payload is also accepted.
          critique  the BrandCriticAgent result (or a bare critique dict, or a
                    list of tweaks). Its prompt_tweaks drive the refinement.
          smooth    when true (default) and a gateway is configured, ask the
                    model to rewrite the merged prompt into natural language.

        Returns {status, refined_prompt, applied_tweaks, base_prompt}. When
        there are no applicable tweaks the base prompt is returned unchanged
        with an empty applied_tweaks list. This agent never calls the image
        engine.
        """
        base_prompt = _resolve_base_prompt(payload)
        if not base_prompt:
            return {"status": "error", "error": "a base prompt is required (asset.prompt)"}

        tweaks = self.extract_tweaks(payload.get("critique"))
        merged, applied = self.merge_tweaks(base_prompt, tweaks)

        if not applied:
            return {
                "status": "ok",
                "refined_prompt": base_prompt,
                "applied_tweaks": [],
                "base_prompt": base_prompt,
                "note": "No applicable tweaks; prompt unchanged.",
            }

        refined = merged
        smoothed = False
        if bool(payload.get("smooth", True)):
            polished = await self._smooth(base_prompt, applied)
            if polished:
                refined = polished
                smoothed = True

        return {
            "status": "ok",
            "refined_prompt": refined,
            "applied_tweaks": applied,
            "base_prompt": base_prompt,
            "smoothed": smoothed,
        }

    async def _smooth(self, base_prompt: str, tweaks: list[str]) -> str | None:
        """Ask the model to rewrite the merge into one natural prompt.

        Returns the rewritten prompt, or None when the gateway is absent or the
        answer is empty so run falls back to the deterministic merge.
        """
        bullet_list = "\n".join(f"- {tweak}" for tweak in tweaks)
        user = (
            f"Base prompt:\n{base_prompt}\n\n"
            f"Apply these edits:\n{bullet_list}\n\n"
            "Return one rewritten prompt that incorporates every edit and keeps "
            "the base subject."
        )
        raw = await self.chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.3,
        )
        if raw is None:
            return None
        text = raw.strip()
        return text or None


def _resolve_base_prompt(payload: dict[str, Any]) -> str:
    """Find the base prompt on the payload, tolerating a few shapes."""
    asset = payload.get("asset")
    if isinstance(asset, dict):
        for key in ("prompt", "text"):
            value = asset.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    top = payload.get("prompt")
    if isinstance(top, str) and top.strip():
        return top.strip()
    return ""


def _strip_trailing_period(text: str) -> str:
    """Drop a trailing period so directives join cleanly without doubling."""
    return text[:-1] if text.endswith(".") else text
