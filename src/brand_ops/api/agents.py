"""Run the in-module agents from the REST layer.

This is the thin bridge the API uses to drive the agents that live in
``brand_ops.agents``. It does two small things and nothing else: resolve an
agent name to its class through the registry, and run that agent against a
payload. The registry is the one source for the name -> class binding, so the
REST surface, the MCP server, and the Brigade manifest all agree on which
class backs each name and can never drift.

This layer constructs no Brigade client and reads no secret. It hands the agent
the shared ``Settings`` and lets the agent degrade on its own when the LLM
gateway or Brigade is absent (the agents return a ``status`` like ``skipped``
rather than raising), so the route can surface that as a normal 200.
"""
from __future__ import annotations

from typing import Any

from ..agents.base import Agent
from ..agents.registry import AGENT_REGISTRY, AgentSpec
from ..config import Settings


class UnknownAgent(KeyError):
    """Raised when a name does not match any registered agent.

    A KeyError subclass so callers can catch it narrowly; the route maps it to
    an HTTP 404. The message lists the known agent names.
    """


def _registry_by_name() -> dict[str, AgentSpec]:
    """The registry indexed by agent name. Built fresh; the registry is a tuple."""
    return {spec.name: spec for spec in AGENT_REGISTRY}


def resolve_agent_cls(name: str) -> type[Agent]:
    """Map an agent name to its class via the registry.

    The registry already binds each name to its ``agent_cls``, so there is no
    separate hand-maintained map to drift. Raises ``UnknownAgent`` for an
    unrecognized name.
    """
    spec = _registry_by_name().get(name)
    if spec is None:
        known = ", ".join(sorted(spec.name for spec in AGENT_REGISTRY))
        raise UnknownAgent(f"unknown agent {name!r}. Known agents: {known}")
    return spec.agent_cls


async def run_agent(
    name: str,
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Instantiate the named agent and await its run against ``payload``.

    Resolves the class through the registry, constructs it with the shared
    settings (the agent builds its own Brigade client from those), and returns
    the agent's result dict unchanged. Raises ``UnknownAgent`` for an unknown
    name; any exception the agent raises propagates to the caller, which maps it
    to an HTTP 502.
    """
    agent_cls = resolve_agent_cls(name)
    agent = agent_cls(settings=settings)
    return await agent.run(payload)
