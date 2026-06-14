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
        workspace_id: str | None = None,
        agent_kind: str = "specialist",
        agent_ref: str | None = None,
        brand_slug: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.brigade = brigade or BrigadeClient(self.settings)
        # Optional metering context. When workspace_id is set, chat() records a
        # best-effort spend event after each LLM turn (see _meter_chat). All four
        # are optional and default to safe values so every existing call site
        # (which passes none of them) is unaffected and unmetered. agent_ref
        # defaults to the agent's stable name.
        self.workspace_id = workspace_id
        self.agent_kind = agent_kind
        self.agent_ref = agent_ref or self.name
        self.brand_slug = brand_slug

    @property
    def _meter_enabled(self) -> bool:
        """True when this agent should record spend (a workspace is in scope)."""
        return bool(self.workspace_id)

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

        When metering is enabled (this agent was constructed with a workspace_id),
        a best-effort spend event is recorded from the gateway's reported token
        usage AFTER the call. Metering never raises into this path and never
        changes the returned text: a missing database, an unset DATABASE_URL, or
        any error is swallowed, so an agent run is identical with or without it.
        """
        resolved_model = self._resolve_model(model)
        reply = await self.brigade.llm_chat(
            messages,
            model=resolved_model,
            temperature=temperature,
            response_json=response_json,
        )
        if self._meter_enabled:
            await self._meter_chat(resolved_model)
        return reply

    async def _meter_chat(self, model: str) -> None:
        """Record one best-effort spend event for the last LLM turn. Never raises.

        Reads the usage the BrigadeClient captured on the just-completed
        ``llm_chat`` (``self.brigade.last_usage``), estimates a cost, and writes
        an ``agent_spend_event`` scoped to this agent's workspace. Everything is
        lazy-imported and wrapped so a metering failure -- no gateway usage, no
        database, an unset DATABASE_URL (get_sessionmaker raises), an RLS or
        transport error -- is silently dropped. This is the worst-outcome guard:
        a broken agent path is unacceptable, missing a spend row is not.
        """
        try:
            usage = self.brigade.last_usage
            # Lazy import so importing the agent never pulls in the DB stack and a
            # CLI/test with no DATABASE_URL is unaffected.
            from .. import governance
            from ..db.base import get_sessionmaker, workspace_session
            from ..db.repository import record_spend_event

            def _as_int(value: Any) -> int | None:
                return value if isinstance(value, int) and not isinstance(value, bool) else None

            prompt_tokens = completion_tokens = total_tokens = None
            if isinstance(usage, dict):
                prompt_tokens = _as_int(usage.get("prompt_tokens"))
                completion_tokens = _as_int(usage.get("completion_tokens"))
                total_tokens = _as_int(usage.get("total_tokens"))
            cost_cents = governance.estimate_cost_cents(model, usage)

            sessionmaker = get_sessionmaker()  # raises if DATABASE_URL is unset
            async with sessionmaker() as session:
                async with workspace_session(session, self.workspace_id) as scoped:
                    await record_spend_event(
                        scoped,
                        self.workspace_id,
                        agent_kind=self.agent_kind,
                        agent_ref=self.agent_ref,
                        kind="chat",
                        brand_slug=self.brand_slug,
                        model=model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        cost_cents=cost_cents,
                    )
        except Exception:  # noqa: BLE001 - metering is best-effort; never break a run
            pass

    @abstractmethod
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent against a payload and return a result object."""
        raise NotImplementedError
