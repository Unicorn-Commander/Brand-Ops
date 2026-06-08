"""AvatarAgent: plan an avatar set and dispatch it to the engine.

Given {brand, count, style, references}, the agent plans a set of avatar items
and generates each through engine.generate_asset with the 'avatar' asset type.
Planning is pure: plan_avatars expands a style and count into a deterministic
list of variants with no LLM and no network.
"""
from __future__ import annotations

from typing import Any

from ..prompt_builder import DEFAULT_PROMPTS
from .base import Agent

# The avatar variants the prompt builder knows about. Sourced from the template
# so the agent never plans a variant the engine would reject.
AVATAR_VARIANTS: tuple[str, ...] = tuple(DEFAULT_PROMPTS["avatar"].variants)
_DEFAULT_VARIANT = "portrait"
_MAX_COUNT = 12


class AvatarAgent(Agent):
    """Plan and generate a set of on-brand avatars."""

    name = "avatar"
    description = "Plan an avatar set for a brand and generate each through the engine."

    # Default (Qwen-first) model; avatar planning is deterministic.
    model = None

    @staticmethod
    def plan_avatars(count: int, style: str | None = None) -> list[str]:
        """Expand a count and style into a deterministic list of variants.

        Pure: no LLM, no network. When style names a known avatar variant, every
        item uses it. Otherwise the known variants are cycled so a larger set
        gets visual range. count is clamped to a sane range.
        """
        n = max(1, min(int(count or 1), _MAX_COUNT))
        chosen = (style or "").strip().lower()
        if chosen in AVATAR_VARIANTS:
            return [chosen] * n
        # No explicit known style: cycle the catalog for variety, starting at a
        # stable default.
        order = (_DEFAULT_VARIANT, *[v for v in AVATAR_VARIANTS if v != _DEFAULT_VARIANT])
        return [order[i % len(order)] for i in range(n)]

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Plan {brand, count, style, references} and generate each avatar.

        payload keys:
          brand       the brand slug (required)
          count       how many avatars to plan (default 1)
          style       a preferred avatar variant, or free text
          references  reference image keys (informational; the engine resolves
                      the brand's own references from the kit)
          dry_run     when true, plan and build prompts but do not call a backend
          backend     optional backend override

        Returns a dict with the planned variants and a per-item outcome list.
        """
        brand = str(payload.get("brand", "")).strip()
        if not brand:
            return {"status": "error", "error": "brand is required"}

        count = payload.get("count", 1)
        style = payload.get("style")
        variants = self.plan_avatars(count, style)
        references = payload.get("references") or []
        dry_run = bool(payload.get("dry_run", True))
        backend = payload.get("backend")

        from ..engine import generate_asset

        items: list[dict[str, Any]] = []
        for variant in variants:
            entry: dict[str, Any] = {"asset_type": "avatar", "variant": variant}
            try:
                outcome = generate_asset(
                    brand,
                    "avatar",
                    variant=variant,
                    backend=backend,
                    dry_run=dry_run,
                    settings=self.settings,
                )
                entry["prompt"] = outcome.built.text
                entry["planned_path"] = str(outcome.planned_path)
                entry["paths"] = [str(p) for p in outcome.paths]
                entry["status"] = "planned" if dry_run else "generated"
            except Exception as exc:  # noqa: BLE001 - report per item, do not abort the set
                entry["status"] = "error"
                entry["error"] = f"{type(exc).__name__}: {exc}"
            items.append(entry)

        return {
            "status": "ok",
            "brand": brand,
            "count": len(items),
            "style": style,
            "references": list(references),
            "dry_run": dry_run,
            "items": items,
        }
