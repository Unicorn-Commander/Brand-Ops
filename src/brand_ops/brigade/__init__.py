"""Unicorn Brigade integration for Brand-Ops.

Brand-Ops owns its agents in-module and registers them with Brigade, the suite
hub. Heavy shared web research is delegated to Brigade's existing deep-research
agent over REST rather than reimplemented here. The agents also reach an
OpenAI-compatible LLM gateway through the same client so every agent shares one
network path and one set of failure modes.
"""
from __future__ import annotations

from .client import BrigadeClient, BrigadeUnavailable

__all__ = ["BrigadeClient", "BrigadeUnavailable"]
