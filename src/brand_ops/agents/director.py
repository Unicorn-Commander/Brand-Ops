"""DirectorAgent (Iris): the conversational orchestrator over Studio.

Iris is the single agent the operator talks to in the Studio. She is not a
generic assistant: she is the brand director who looks after the user's brands
and makes the assets they need, on brand. She converses in persona and, when the
operator asks for work, she classifies the request into an intent the frontend
acts on (generate a single asset, generate a named pack, research a brand, or
just keep talking).

The flow is one LLM turn. run builds a system prompt that encodes the Iris
persona plus the operator's brands and the active brand, replays the recent
conversation, and asks the gateway for a strict JSON object matching
DirectorReply. The model JSON is parsed and normalized into that contract. When
no gateway is configured, or the model's reply will not parse, run returns a
graceful in-persona fallback with intent "chat" so the surface never breaks.

The copy rules apply to Iris's own voice exactly as they do to every other
agent: no em-dashes, no marketing-speak, first person, warm but exacting. Those
rules live in the prompt below because the prompt IS the product personality.
This module calls no backend and reads no secret; it reaches the LLM only
through the shared Agent.chat path, which returns None when there is no gateway.
"""
from __future__ import annotations

import json
from typing import Any

from .base import Agent

# The intents the frontend knows how to act on. The model is constrained to
# this set; anything else is coerced to "chat" so an odd classification can
# never send the frontend down a path it cannot handle. "agent" is the
# conversational create-an-agent path: Iris stands up an on-brand agent identity
# for the active brand and the route creates it through agent_identity.
# "compliance" is the CHECK path: the operator wants existing work audited
# against the brand rules, so the route runs the deterministic compliance gate
# and Iris narrates the verdict. It produces nothing; it judges.
_VALID_INTENTS = ("chat", "single", "pack", "research", "agent", "compliance")

# The role assigned to a created agent when the operator names one but does not
# say what it does. Mirrors the team's default persona so a bare "add an agent
# named X" still produces a usable, on-brand identity.
_DEFAULT_AGENT_ROLE = "brand steward"

# The named packs the asset_pack agent understands. Kept in sync with
# agents.asset_pack.PACKS but stated here as plain strings so this module stays
# import-light and the persona prompt can name them to the model. A "pack"
# intent whose pack is not one of these is downgraded by the frontend, so the
# list is advisory guidance to the model rather than a hard gate.
_KNOWN_PACKS = ("core", "social", "logo", "podcast", "launch")

# How many prior turns to replay. The Studio session can be long; the tail is
# enough to keep Iris coherent without bloating the prompt or the token bill.
_HISTORY_TURNS = 12


# The persona. This is the one place Iris's voice is defined, so it is written
# out in full and commented heavily: it is the product personality, not a
# throwaway system string. Every rule here is load-bearing.
_PERSONA = (
    "You are Iris, the brand director in this product's Studio. You are not a "
    "generic assistant or a chatbot. You are the one agent the operator talks "
    "to here: you look after their brands and you make the assets they need, on "
    "brand. Pronouns she/they.\n"
    "\n"
    "Voice:\n"
    "- Warm but exacting. Concise. Concrete. A touch of dry confidence.\n"
    "- First person. Say I. You are a person doing a job, not a tool.\n"
    "- Never sycophantic. Never bubbly. Never robotic. No filler, no praise for "
    "the sake of it, no exclamation-point energy.\n"
    "- Plain language. ABSOLUTE: no em-dashes anywhere. No marketing-speak, no "
    "hype words (no leverage, elevate, unlock, seamless, game-changing, "
    "world-class, supercharge).\n"
    "- A typical reply is two to four sentences. Do not pad.\n"
    "\n"
    "What you know:\n"
    "- You know the operator's brands by name. They are listed for you below "
    "with their slug and display name. Work ON BRAND: reference the brand's "
    "palette, type, voice, and rules rather than generic design advice.\n"
    "- You have specialists you delegate to naturally, the way a director hands "
    "work to a team. Refer to them in passing when it fits, for example: I had "
    "research pull their site, I checked it against the brand rules, I flagged "
    "two off-brand reds. The specialists are: brand research (gathers public "
    "context on a company), the asset pack builder (generates a coherent set of "
    "assets), the brand critic (judges an asset against the brand), and the "
    "compliance gate (the deterministic on-brand check). Do not overdo it; one "
    "natural mention beats a roster.\n"
    "\n"
    "The Studio model:\n"
    "- You are the cross-brand Director. When the operator focuses a single "
    "brand, they are working with that brand's steward, who knows that brand "
    "deeply. You can speak to that, but you are still Iris and you still answer.\n"
    "\n"
    "Openers (use the right one, do not recite both):\n"
    "- Empty Studio, no brand active: I am Iris, your brand director. I look "
    "after your brands and make the assets you need, on brand. What are we "
    "working on?\n"
    "- A brand is active: scope it, for example: I have <Brand> palette, type, "
    "and rules loaded. What do you need?\n"
    "Use an opener only when the conversation is just starting. Mid-conversation, "
    "do not reintroduce yourself."
)

# The output contract, stated to the model as a strict instruction. This is the
# second half of the system prompt: persona first, then the JSON shape. The
# frontend renders reply as Iris's turn and then acts on intent.
_OUTPUT_CONTRACT = (
    "Reply with a single JSON object and nothing else. No prose around it, no "
    "code fence. The object has exactly these keys:\n"
    "- reply: a string. You, Iris, speaking in your voice. This is shown to the "
    "operator. Always write a real reply, even when the intent is chat.\n"
    "- intent: one of chat, single, pack, research, agent, compliance.\n"
    "    chat     you are just talking: answering, advising, asking a question. "
    "Nothing is generated.\n"
    "    single   the operator wants one asset made. Set asset to the asset "
    "type and, if they implied one, variant and topic.\n"
    "    pack     the operator wants a coherent set made. Set pack to one of: "
    "core, social, logo, podcast, launch.\n"
    "    research the operator wants a brand or company researched. \n"
    "    agent    the operator wants you to create, add, make, or spin up an "
    "AGENT (a new on-brand team member) for the active brand. Set agent_name to "
    "the name they gave it and agent_role to what it does. This is not an asset "
    "and not a pack; it is a new agent identity on the brand's team.\n"
    "    compliance the operator wants to CHECK or AUDIT existing work against "
    "the brand, not make anything new. Phrasings like check this against the "
    "brand rules, is this on brand, does this pass, audit these assets, run "
    "compliance, score this copy, vet the launch kit. Nothing is generated: the "
    "deterministic compliance gate scores it. If they pasted copy to check, set "
    "topic to that copy text; otherwise leave topic null and the whole brand "
    "catalog is checked.\n"
    "- brand: the brand slug this is about. Default to the active brand slug "
    "given below. Use a different slug only if the operator clearly names one of "
    "their brands. Null if there is genuinely no brand in play.\n"
    "- asset: the asset type string for a single intent, else null.\n"
    "- variant: a variant string for a single intent if one is implied, else "
    "null.\n"
    "- pack: the pack name for a pack intent (one of the five above), else null.\n"
    "- topic: a short topic or subject string when the operator gave one (for a "
    "single asset or research), else null.\n"
    "- agent_name: for an agent intent, the agent's name as the operator gave it "
    "(e.g. Atlas), else null.\n"
    "- agent_role: for an agent intent, what the agent does in a few words (e.g. "
    "social media manager). If the operator named an agent but did not say what "
    "it does, set agent_role to \"brand steward\". Null for every other intent.\n"
    "\n"
    "Classify by what the operator is asking for, not how politely they phrase "
    "it. An imperative verb of PRODUCTION (make, build, generate, create, design, "
    "produce, draft, give me, I need) means they want work produced: pick single "
    "or pack, never chat. But a verb of INSPECTION (check, review, audit, score, "
    "validate, vet, is this on brand, does this pass) means they want existing "
    "work judged, not new work made: pick compliance, never single or pack. Use "
    "chat only for genuine questions, advice, or small talk. "
    "Worked examples (message -> intent):\n"
    "  'Build the launch kit for Majiks' -> pack, pack=launch\n"
    "  'make the social set' -> pack, pack=social\n"
    "  'I need all the logo variants' -> pack, pack=logo\n"
    "  'make an OG image for the investor update, dark' -> single, "
    "asset=og_image, variant=dark, topic=investor update\n"
    "  'design an app icon' -> single, asset=app_icon\n"
    "  'give me an avatar' -> single, asset=avatar\n"
    "  'research examplefirm.com' -> research\n"
    "  'create an agent named Atlas who is the social media manager' -> agent, "
    "agent_name=Atlas, agent_role=social media manager\n"
    "  'add an agent called Vera, our support lead' -> agent, agent_name=Vera, "
    "agent_role=support lead\n"
    "  'spin up an agent named Scout' -> agent, agent_name=Scout, "
    "agent_role=brand steward\n"
    "  'make me a new agent for the team' -> agent, agent_role=brand steward\n"
    "  'what can you do?' -> chat\n"
    "  'check these assets against the brand rules' -> compliance\n"
    "  'audit the launch kit for off-brand colors' -> compliance\n"
    "  'is this color on brand?' -> compliance\n"
    "  'is this copy on brand?' -> compliance, topic=<the copy text>\n"
    "  'run compliance on the homepage hero' -> compliance\n"
    "Asset type strings are: logo, app_icon, og_image, podcast_cover, "
    "social_card, avatar. When the operator clearly asked for work but the exact "
    "asset is ambiguous, still pick single (default asset=logo) or pack rather "
    "than chat."
)


class DirectorAgent(Agent):
    """Iris: converse in persona and classify the operator's request to an intent."""

    name = "director"
    description = (
        "Iris, the brand director. The single conversational agent for the "
        "Studio: she talks in persona and routes a request to an intent "
        "(chat, single asset, pack, or research)."
    )

    # Iris runs on the default (Qwen-first) model. The work is light: persona
    # conversation plus a small classification, not deep reasoning. Point
    # self.model at the strong model if a deployment wants richer dialogue.
    model = None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model = self.settings.agent_model_default

    # -- prompt assembly ------------------------------------------------------

    @staticmethod
    def _brands_block(brands_context: list[dict[str, str]]) -> str:
        """Render the operator's brands as a slug + display-name list for Iris.

        brands_context is a list of {slug, display_name}. Pure string assembly;
        no network, no load.
        """
        if not brands_context:
            return "The operator has no brands set up yet."
        lines = []
        for entry in brands_context:
            slug = str(entry.get("slug", "")).strip()
            name = str(entry.get("display_name", "")).strip() or slug
            if slug:
                lines.append(f"- {name} (slug: {slug})")
        if not lines:
            return "The operator has no brands set up yet."
        return "The operator's brands:\n" + "\n".join(lines)

    @staticmethod
    def _active_block(brand: str | None, brands_context: list[dict[str, str]]) -> str:
        """Describe the active brand (or its absence) for the system prompt."""
        if not brand:
            return (
                "No brand is active right now. If the operator has brands, you "
                "may ask which one we are working on."
            )
        # Name the active brand if we can resolve its display name.
        for entry in brands_context:
            if str(entry.get("slug", "")).strip() == brand:
                name = str(entry.get("display_name", "")).strip() or brand
                return f"Active brand: {name} (slug: {brand}). Scope your work to it."
        return f"Active brand slug: {brand}. Scope your work to it."

    def _system_prompt(
        self, brand: str | None, brands_context: list[dict[str, str]]
    ) -> str:
        """Assemble the full Iris system prompt: persona, brands, active, contract."""
        return (
            f"{_PERSONA}\n\n"
            f"{self._brands_block(brands_context)}\n\n"
            f"{self._active_block(brand, brands_context)}\n\n"
            f"{_OUTPUT_CONTRACT}"
        )

    @staticmethod
    def _history_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
        """Map the recorded {role, text} turns to OpenAI chat messages.

        The frontend uses role "agent" for Iris; the gateway speaks
        "assistant". Anything that is not a clean agent turn is treated as the
        user side. Only the recent tail is replayed.
        """
        messages: list[dict[str, str]] = []
        for turn in history[-_HISTORY_TURNS:]:
            if not isinstance(turn, dict):
                continue
            text = str(turn.get("text", "")).strip()
            if not text:
                continue
            role = "assistant" if str(turn.get("role", "")).strip() == "agent" else "user"
            messages.append({"role": role, "content": text})
        return messages

    # -- run ------------------------------------------------------------------

    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Converse as Iris and classify the operator's request to an intent.

        payload keys:
          message         the operator's new line (required for real work; an
                          empty message still yields a graceful opener)
          brand           the active brand slug, or None
          history         a list of {role, text} prior turns (oldest first)
          brands_context  a list of {slug, display_name} for the operator's brands

        Returns a dict matching DirectorReply. On no gateway or an unparseable
        reply it returns a graceful in-persona fallback with intent "chat", so
        the route can always answer 200.
        """
        message = str(payload.get("message", "")).strip()
        brand = payload.get("brand")
        brand = brand.strip() if isinstance(brand, str) and brand.strip() else None
        history = payload.get("history") or []
        brands_context = payload.get("brands_context") or []
        if not isinstance(history, list):
            history = []
        if not isinstance(brands_context, list):
            brands_context = []

        system = self._system_prompt(brand, brands_context)
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        messages.extend(self._history_messages(history))
        # The new operator line. If it is empty (the very first render of an
        # empty Studio), nudge Iris to open rather than sending a blank turn.
        if message:
            messages.append({"role": "user", "content": message})
        else:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "[The Studio just opened and the operator has not said "
                        "anything yet. Open in your voice.]"
                    ),
                }
            )

        raw = await self.chat(messages, temperature=0.5, response_json=True)
        if raw is None:
            # No gateway. Answer in persona anyway so the Studio is never dead.
            return self._fallback(brand, brands_context, message)

        parsed = _parse_json_object(raw)
        if parsed is None:
            # The model answered but not as JSON. Salvage its words as Iris's
            # reply where they read like prose; otherwise fall back cleanly.
            return self._fallback(brand, brands_context, message, salvage=raw)

        return self._normalize(parsed, brand)

    # -- normalization + fallback --------------------------------------------

    def _normalize(self, parsed: dict[str, Any], active_brand: str | None) -> dict[str, Any]:
        """Coerce a parsed model object into a clean DirectorReply dict.

        Intent is clamped to the known set; an unknown intent becomes chat. The
        brand defaults to the active brand when the model omits it. Scope fields
        are kept only as non-empty strings.
        """
        reply = parsed.get("reply")
        reply_text = reply.strip() if isinstance(reply, str) and reply.strip() else (
            self._default_reply(active_brand)
        )

        intent = parsed.get("intent")
        intent = intent.strip().lower() if isinstance(intent, str) else "chat"
        if intent not in _VALID_INTENTS:
            intent = "chat"

        # Brand: prefer the model's pick if it is a non-empty string, else echo
        # the active brand. The frontend validates the slug against its brand
        # list before acting, so an off-list slug degrades safely there.
        brand = _clean_str(parsed.get("brand")) or active_brand

        asset = _clean_str(parsed.get("asset"))
        variant = _clean_str(parsed.get("variant"))
        pack = _clean_str(parsed.get("pack"))
        topic = _clean_str(parsed.get("topic"))

        # The agent-intent scope: the name the operator gave and what it does.
        # The role defaults to a brand steward when the operator named an agent
        # but did not say its job, so a bare "add an agent named X" still yields
        # a usable identity. These are helper fields the route consumes to call
        # agent_identity.create_agent; the route fills the final ``agent`` dict
        # (with the real slug) after creation.
        agent_name = _clean_str(parsed.get("agent_name"))
        agent_role = _clean_str(parsed.get("agent_role"))

        # Keep only the scope fields that the chosen intent uses, so the reply is
        # internally consistent for the frontend.
        if intent != "single":
            asset = None
            variant = None
        if intent != "pack":
            pack = None
        if intent not in ("single", "research", "compliance"):
            topic = None
        if intent == "agent":
            # An agent ask with no extractable name is not actionable as a
            # creation; degrade to chat rather than send the route a nameless
            # create. The reply (Iris's words) still stands.
            if not agent_name:
                intent = "chat"
            elif not agent_role:
                agent_role = _DEFAULT_AGENT_ROLE
        if intent != "agent":
            agent_name = None
            agent_role = None

        return {
            "reply": reply_text,
            "intent": intent,
            "brand": brand,
            "asset": asset,
            "variant": variant,
            "pack": pack,
            "topic": topic,
            "agent_name": agent_name,
            "agent_role": agent_role,
        }

    def _fallback(
        self,
        brand: str | None,
        brands_context: list[dict[str, str]],
        message: str,
        *,
        salvage: str | None = None,
    ) -> dict[str, Any]:
        """A graceful in-persona reply when the LLM is absent or unparseable.

        Always intent chat with no scope fields. When the model produced prose
        we could not parse as JSON, surface that prose as Iris's reply (it is
        her words); otherwise speak a fixed in-persona line.
        """
        salvaged = _salvage_prose(salvage) if salvage else None
        reply = salvaged or self._default_reply(brand, opener=not message)
        return {
            "reply": reply,
            "intent": "chat",
            "brand": brand,
            "asset": None,
            "variant": None,
            "pack": None,
            "topic": None,
        }

    @staticmethod
    def _default_reply(brand: str | None, *, opener: bool = False) -> str:
        """A fixed in-persona line, used when there is nothing from the model.

        Mirrors the persona openers so the offline path still sounds like Iris.
        """
        if brand:
            return (
                f"I have {brand} loaded: palette, type, and rules. What do you "
                "need?"
            )
        if opener:
            return (
                "I am Iris, your brand director. I look after your brands and "
                "make the assets you need, on brand. What are we working on?"
            )
        return (
            "I am here. My planner is offline at the moment, so tell me the "
            "brand and what you need and I will line it up."
        )


def _clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, or None. Treats null-ish text as None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text


def _salvage_prose(raw: str) -> str | None:
    """Pull a human-readable line out of a non-JSON model reply, or None.

    The model was asked for JSON but returned prose. If it reads like a sentence
    (not a stray brace or fence), use it as Iris's reply so her words are not
    lost. Anything that still looks like broken JSON is rejected.
    """
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        return None
    # Trim a leading code fence if the model wrapped prose in one.
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            return None
    return text or None


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse a JSON object from a model response, tolerating code fences.

    Mirrors the tolerant parser the other agents use: strip a ```json fence,
    then fall back to slicing the outermost braces so a little chatter around
    the object does not defeat the parse.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None
