"""Async client for Unicorn Brigade plus the OpenAI-compatible LLM gateway.

Two outbound paths live here so the agents have one place to configure network
behavior:

  research(topic)      delegate web research to Brigade's deep-research agent
  llm_chat(messages)   call the OpenAI-compatible chat gateway

Both degrade clean when the relevant url or key is missing. research raises
BrigadeUnavailable with a clear message; llm_chat returns None. This lets the
agents stay importable and unit-testable in an environment with no secrets and
no network, which is the build and CI posture for this module.

httpx is lazy-imported inside the methods so importing this module never pulls
in the http stack and never touches the network.
"""
from __future__ import annotations

from typing import Any

from ..config import Settings, get_settings

# Defensive cap so a hung gateway cannot stall an agent run forever. Research is
# a longer operation than a single chat turn, so it gets a wider budget.
_CHAT_TIMEOUT_SECONDS = 60.0
_RESEARCH_TIMEOUT_SECONDS = 180.0


class BrigadeUnavailable(RuntimeError):
    """Raised when a Brigade call cannot be made or the hub returns an error.

    The message says which piece of configuration is missing or which call
    failed, so the caller can act without reading a stack trace.
    """


class BrigadeClient:
    """Holds Brigade research and OpenAI-compatible chat for the agents.

    The client is configuration-only at construction time. No connection is
    opened until a method is called, and a fresh httpx.AsyncClient is opened and
    closed per call to keep the surface stateless and safe to share.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # The usage object (prompt_tokens/completion_tokens/total_tokens) from the
        # most recent llm_chat call, or None when none ran or the gateway returned
        # none. This is the spend-capture point: llm_chat itself returns only the
        # message text (its contract is unchanged), so a caller that wants to meter
        # reads this AFTER the call. Reset to None at the start of every llm_chat.
        self.last_usage: dict[str, Any] | None = None

    # -- Brigade deep research ------------------------------------------------

    def _research_ready(self) -> bool:
        s = self.settings
        return bool(s.brigade_base_url and s.brigade_api_key)

    async def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        """Delegate web research to Brigade's deep-research agent.

        POSTs to {brigade_base_url}/api/v1/agents/research with the
        X-API-Key header. Returns the parsed JSON object from the hub.

        Raises BrigadeUnavailable when the base url or api key is missing, or
        when the hub returns a transport or non-object error.
        """
        if not topic or not topic.strip():
            raise BrigadeUnavailable("research called with an empty topic")
        if not self._research_ready():
            raise BrigadeUnavailable(
                "Brigade is not configured. Set BRIGADE_BASE_URL and "
                "BRIGADE_API_KEY to enable web research."
            )

        import httpx

        url = f"{self.settings.brigade_base_url.rstrip('/')}/api/v1/agents/research"
        headers = {"X-API-Key": self.settings.brigade_api_key}
        body = {"topic": topic, "depth": depth}
        try:
            async with httpx.AsyncClient(timeout=_RESEARCH_TIMEOUT_SECONDS) as http:
                response = await http.post(url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise BrigadeUnavailable(f"Brigade research request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise BrigadeUnavailable("Brigade research returned a non-object JSON payload")
        return payload

    # -- OpenAI-compatible chat gateway ---------------------------------------

    def _chat_ready(self) -> bool:
        s = self.settings
        return bool(s.llm_base_url and s.llm_api_key)

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        temperature: float = 0.4,
        response_json: bool = False,
    ) -> str | None:
        """Call the OpenAI-compatible chat gateway and return the message text.

        POSTs to {llm_base_url}/chat/completions with an Authorization Bearer
        header. model defaults to settings.agent_model_default. Returns the
        assistant message content as a string, or None when the gateway is not
        configured (no base url or no api key) so the agents can fall back to
        their pure planning paths without raising.

        response_json asks the gateway for a JSON object response where the
        backend supports it.
        """
        # Reset usage at the top so last_usage always reflects THIS call: every
        # early return below (no messages, no gateway, transport error) leaves it
        # None, and only a parsed success sets it. Never affects the return value.
        self.last_usage = None
        if not messages:
            return None
        if not self._chat_ready():
            return None

        import httpx

        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        body: dict[str, Any] = {
            "model": model or self.settings.agent_model_default,
            "messages": messages,
            "temperature": temperature,
        }
        if response_json:
            body["response_format"] = {"type": "json_object"}
        try:
            async with httpx.AsyncClient(timeout=_CHAT_TIMEOUT_SECONDS) as http:
                response = await http.post(url, headers=headers, json=body)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError:
            # The gateway is optional from the agents' point of view: a network
            # or auth failure means "no LLM available", same as no key set.
            return None
        # Capture the gateway's token usage for best-effort spend metering before
        # discarding the rest of the payload. This does NOT change the return type
        # or value: callers still get only the message text.
        self.last_usage = payload.get("usage") if isinstance(payload, dict) else None
        return _first_message_content(payload)


def _first_message_content(payload: Any) -> str | None:
    """Pull the assistant content out of an OpenAI-compatible response."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None
