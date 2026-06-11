"""Brand Compliance Loop: machine-readable brand truth + a deterministic checker.

Two halves, both pure and LLM-free:

* ``tokens`` and ``rules`` project a loaded ``Brand`` into machine-readable
  design tokens and a rules manifest. This is the brand's truth as data,
  ready for agents, the rest of the suite, and a CSS copy-paste.
* ``evaluator`` scores an asset's pixels or a piece of copy against those
  rules with a fixed, reproducible algorithm. The same input always yields
  the same verdict, so the result is auditable and there is no model to drift.

Everything here is importable without Pillow; the color check degrades to a
"skipped" status when Pillow is absent or the file is a vector/unreadable.
"""
from __future__ import annotations

from .evaluator import (
    check_asset,
    check_image_colors,
    check_text,
    check_text_target,
)
from .lifecycle import (
    LIFECYCLE_STATES,
    backfill_compliance,
    stamp_compliance,
    transition,
)
from .rules import build_rules
from .tokens import build_tokens

__all__ = [
    "build_tokens",
    "build_rules",
    "check_text",
    "check_image_colors",
    "check_asset",
    "check_text_target",
    "stamp_compliance",
    "backfill_compliance",
    "transition",
    "LIFECYCLE_STATES",
]
