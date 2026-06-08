"""The minimal agent base class.

This is the pilot agent runtime for the suite. It stays small on purpose: a
name, a default model, a shared Brigade client, and an async run that every
concrete agent implements. The plan is to lift this into a suite SDK once a
second app needs it, so it carries no Brand-Ops specifics and no framework.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..brigade import BrigadeClient
from ..config import Settings, get_settings


class Agent(ABC):
    """Base for an in-module agent.

    Subclasses set a class-level name and override run. They reach the LLM
    through self.chat, which routes to the shared BrigadeClient and applies the
    agent's default model unless a call overrides it.
    """

    name: str = "agent"
    description: str = ""
    # The default model id for this agent. Subclasses pick the strong model for
    # reasoning-heavy work (research, critique) and the default model otherwise.
    model: str | None = None

    def __init__(
        self,
        *,
        brigade: BrigadeClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.brigade = brigade or BrigadeClient(self.settings)

    def _resolve_model(self, model: str | None) -> str:
        return model or self.model or self.settings.agent_model_default

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.4,
        response_json: bool = False,
    ) -> str | None:
        """Send a chat turn through the shared gateway.

        Returns the assistant text, or None when no gateway is configured so the
        caller can fall back to a deterministic path.
        """
        return await self.brigade.llm_chat(
            messages,
            model=self._resolve_model(model),
            temperature=temperature,
            response_json=response_json,
        )

    @abstractmethod
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent against a payload and return a result object."""
        raise NotImplementedError
