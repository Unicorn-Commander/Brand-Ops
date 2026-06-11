"""AssetPackAgent: plan a pack of assets and drive the engine to make them.

The planning logic is pure: plan_pack maps a named pack to a deterministic list
of (asset_type, variant) items with no LLM and no network, so it is fully
unit-testable. run then walks that plan and calls engine.generate_asset for each
item, honoring dry_run so a plan can be previewed without hitting an image
backend.

The LLM is used only as an optional layer for free-form pack requests; the
built-in packs never need it.
"""
from __future__ import annotations

from datetime import UTC
from typing import Any, NamedTuple

from .base import Agent


class PackItem(NamedTuple):
    """One planned asset: a type and an optional variant."""

    asset_type: str
    variant: str | None


# Named packs map to an ordered, deterministic list of assets. These are the
# common bundles a brand needs at once. Each entry is (asset_type, variant);
# variants line up with the templates in prompt_builder.DEFAULT_PROMPTS.
PACKS: dict[str, tuple[tuple[str, str | None], ...]] = {
    "core": (
        ("logo", "color"),
        ("logo", "monochrome"),
        ("app_icon", "color"),
        ("og_image", "default"),
    ),
    "social": (
        ("social_card", "instagram"),
        ("social_card", "twitter"),
        ("social_card", "linkedin"),
        ("og_image", "default"),
    ),
    "logo": (
        ("logo", "flat"),
        ("logo", "monochrome"),
        ("logo", "color"),
        ("logo", "app-icon"),
    ),
    "podcast": (
        ("podcast_cover", "series"),
        ("podcast_cover", "episode"),
        ("social_card", "instagram"),
    ),
    "launch": (
        ("logo", "color"),
        ("app_icon", "color"),
        ("og_image", "default"),
        ("social_card", "twitter"),
        ("social_card", "linkedin"),
    ),
}


class AssetPackAgent(Agent):
    """Plan and generate a coherent pack of brand assets."""

    name = "asset_pack"
    description = "Plan a named pack of brand assets and generate each through the engine."

    # Uses the default (Qwen-first) model; packs are deterministic so the LLM is
    # only ever an optional planning aid.
    model = None

    @staticmethod
    def plan_pack(pack: str) -> list[PackItem]:
        """Map a pack name to a deterministic list of assets.

        Pure: no LLM, no network, no settings. Raises KeyError with the known
        pack names when the pack is not recognized, mirroring the engine's
        error style.
        """
        key = (pack or "").strip().lower()
        try:
            items = PACKS[key]
        except KeyError as exc:
            known = ", ".join(sorted(PACKS))
            raise KeyError(f"unknown pack {pack!r}. Known packs: {known}") from exc
        return [PackItem(asset_type, variant) for asset_type, variant in items]

    @staticmethod
    def available_packs() -> list[str]:
        """The list of known pack names, for menus and registry input schemas."""
        return sorted(PACKS)

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Plan {brand, pack} and generate each item through the engine.

        payload keys:
          brand    the brand slug (required)
          pack     a known pack name (required)
          dry_run  when true, plan and build prompts but do not call a backend
          backend  optional backend override passed through to the engine

        Returns a dict with the plan and a per-item outcome list. Generation
        failures are captured per item rather than aborting the whole pack.
        """
        brand = str(payload.get("brand", "")).strip()
        pack = str(payload.get("pack", "")).strip()
        if not brand:
            return {"status": "error", "error": "brand is required"}
        if not pack:
            return {"status": "error", "error": "pack is required"}

        try:
            plan = self.plan_pack(pack)
        except KeyError as exc:
            return {"status": "error", "error": str(exc)}

        dry_run = bool(payload.get("dry_run", True))
        backend = payload.get("backend")

        # Import the engine lazily to keep agent import light and avoid any
        # import-time coupling to the backend stack.
        from ..engine import generate_asset

        items: list[dict[str, Any]] = []
        for item in plan:
            entry: dict[str, Any] = {
                "asset_type": item.asset_type,
                "variant": item.variant,
            }
            try:
                outcome = generate_asset(
                    brand,
                    item.asset_type,
                    variant=item.variant,
                    backend=backend,
                    dry_run=dry_run,
                    settings=self.settings,
                )
                entry["prompt"] = outcome.built.text
                entry["planned_path"] = str(outcome.planned_path)
                entry["paths"] = [str(p) for p in outcome.paths]
                entry["status"] = "planned" if dry_run else "generated"
            except Exception as exc:  # noqa: BLE001 - report per item, do not abort the pack
                entry["status"] = "error"
                entry["error"] = f"{type(exc).__name__}: {exc}"
            items.append(entry)

        # Record one summary event on the provenance feed: the asset_pack agent
        # ran a named pack for this brand. A dry-run is a plan, not a generation,
        # so the note distinguishes the two. Best-effort: a feed failure never
        # changes what the agent returns.
        try:
            from datetime import datetime

            from ..activity import append_event

            verb = "planned asset pack" if dry_run else "ran asset pack"
            append_event(
                "asset_pack",
                "agent",
                verb,
                brand=brand,
                target=f"pack/{pack}",
                note=f"{len(items)} assets",
                ts=datetime.now(UTC).isoformat(),
            )
        except Exception:  # noqa: BLE001 - the feed is advisory; never fail the run
            pass

        return {
            "status": "ok",
            "brand": brand,
            "pack": pack,
            "dry_run": dry_run,
            "count": len(items),
            "items": items,
        }
