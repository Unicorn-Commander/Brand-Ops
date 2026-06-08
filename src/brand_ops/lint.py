"""Copy guardrails. Two hard rules: no em-dashes and no marketing-speak.

Used to check composed prompts, generated captions, and docs before they ship.
Returns a list of human-readable issues. An empty list means clean.
"""
from __future__ import annotations

# Built with chr() so this file holds no literal dash character of its own.
# A spaced hyphen, a comma, or a period is the allowed substitute.
_DASHES = {chr(0x2014): "em-dash", chr(0x2013): "en-dash"}

_MARKETING = (
    "revolutionary",
    "game-changer",
    "game changer",
    "synergize",
    "synergy",
    "unleash",
    "cutting-edge",
    "best-in-class",
    "world-class",
    "next-generation",
    "disrupt",
)


def check_copy(text: str, extra_forbidden: list[str] | None = None) -> list[str]:
    issues: list[str] = []
    for char, name in _DASHES.items():
        if char in text:
            issues.append(
                f"contains a {name}; use a comma, a period, or a spaced hyphen instead"
            )
    lowered = text.lower()
    for phrase in _MARKETING:
        if phrase in lowered:
            issues.append(f"marketing-speak: {phrase!r}")
    for phrase in extra_forbidden or []:
        if phrase.lower() in lowered:
            issues.append(f"brand forbidden phrase: {phrase!r}")
    return issues
