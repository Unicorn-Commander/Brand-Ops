"""Agent governance vocabulary, default standing orders, and a cost estimator.

This is the small, pure, dependency-light core of the agent-governance vertical.
It holds the canonical sets the API validates against (autonomy levels, agent
kinds), the honest seed copy the Crew page shows for an agent that has never been
configured, and a deliberately simple per-token cost ESTIMATE for the spend
ledger.

Nothing here touches the database, the network, or any secret, so the API, the
agents, and the tests all import it freely. The cost estimate in particular is
an approximation to be reconciled against the gateway / Lago later (see
``estimate_cost_cents``); it exists so a metered turn records a non-null,
order-of-magnitude-correct number today rather than nothing.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Canonical vocabulary (the API validates against these)
# ---------------------------------------------------------------------------

# The autonomy postures, least to most autonomous. "propose" is the suite
# default posture (House Rule HR-01: propose, don't publish): the agent drafts
# but a human ratifies any production change. "auto_with_review" lets the agent
# act and surfaces the work for after-the-fact review. "autonomous" lets it act
# without a review gate. v1 stores + displays the posture; it does not yet
# enforce it (no gate is wired off this value).
AUTONOMY_LEVELS: tuple[str, ...] = ("propose", "auto_with_review", "autonomous")

# The kinds of agent governance can attach to. "director" is the one Iris
# persona; "steward" is the per-brand steward (its ref is the brand slug);
# "specialist" is a shared registry agent (its ref is the agent name);
# "identity" is a brand-owned agent identity (its ref can carry a slug, hence the
# ``{agent_ref:path}`` converter on the route).
AGENT_KINDS: tuple[str, ...] = ("director", "steward", "specialist", "identity")

# The posture a never-configured agent reads as, matching House Rule HR-01.
DEFAULT_AUTONOMY: str = "propose"


# ---------------------------------------------------------------------------
# Default standing orders (the honest seed copy the Crew page shows)
# ---------------------------------------------------------------------------

# The Director's standing orders. These reuse the exact wording the Crew page
# hard-coded before this feature (minus the "TODO(backend):" prefix), so the
# page reads identically the first time it loads governance, before any edit.
_DIRECTOR_STANDING_ORDERS: tuple[str, ...] = (
    "Research before proposing. Cite the dossier.",
    "Three directions minimum, one recommendation, defend it.",
    "Spawn a steward at every ratify; set its standing orders.",
)


def default_standing_orders(kind: str, ref: str) -> str:
    """The honest seed standing orders for an agent that has no saved row yet.

    Returns the same copy the Crew page showed as static text before governance
    was persisted, so a freshly-loaded card reads identically until the operator
    edits it. The Director gets its three orders; a steward gets the per-brand
    guard line; everything else gets a neutral, honest default. Pure: no load, no
    network.
    """
    if kind == "director":
        return "\n".join(_DIRECTOR_STANDING_ORDERS)
    if kind == "steward":
        # Mirrors the Crew page's steward line. ``ref`` is the brand slug.
        return "Guard palette, kit coverage, and drift for this brand."
    if kind == "specialist":
        return (
            f"Run {ref} only when asked. Stay on brand. Surface results for "
            "review; do not publish."
        )
    # identity, or any future kind.
    return "Stay on brand. Propose, do not publish. Surface work for review."


# ---------------------------------------------------------------------------
# Cost estimation (an ESTIMATE, reconciled against the gateway/Lago later)
# ---------------------------------------------------------------------------

# A coarse blended price per 1,000 tokens, in US cents, keyed by a substring of
# the model id (matched case-insensitively, first match wins). This is NOT a
# billing source of truth: it is a conservative, order-of-magnitude estimate so a
# metered turn records a non-null cost today. The real per-prompt/per-completion,
# per-model price lives at the gateway/Lago and will reconcile these rows later.
#
# Local models (qwen, the suite's self-hosted inference) are billing-exempt in
# practice (no per-token charge), so they price at ~0. Frontier models price
# higher. The map is intentionally small and documented; extend it as models are
# added rather than chasing exactness here.
_PRICE_PER_1K_CENTS: tuple[tuple[str, float], ...] = (
    # Local / self-hosted: effectively free to run, so ~0 cost.
    ("qwen", 0.0),
    ("llama", 0.0),
    ("gemma", 0.0),
    ("local", 0.0),
    # Frontier / hosted (blended prompt+completion, deliberately conservative):
    ("opus", 4.5),
    ("sonnet", 0.9),
    ("haiku", 0.12),
    ("claude", 0.9),  # unspecified claude -> sonnet-class
    ("gpt-4o-mini", 0.05),
    ("gpt-4o", 0.75),
    ("gpt-4", 3.0),
    ("gpt-3.5", 0.1),
    ("gpt", 0.5),  # unspecified gpt -> mid
    ("gemini", 0.5),
    ("mistral", 0.3),
)

# The conservative fallback when no substring matches: assume a mid-tier hosted
# model rather than free, so an unknown model is never silently costed at zero.
_DEFAULT_PRICE_PER_1K_CENTS: float = 0.5


def _price_per_1k_cents(model: str | None) -> float:
    """The blended price per 1k tokens (cents) for a model id, by substring.

    Case-insensitive, first match in ``_PRICE_PER_1K_CENTS`` wins; an unknown or
    missing model falls back to the conservative mid-tier default.
    """
    if not model:
        return _DEFAULT_PRICE_PER_1K_CENTS
    needle = model.lower()
    for fragment, price in _PRICE_PER_1K_CENTS:
        if fragment in needle:
            return price
    return _DEFAULT_PRICE_PER_1K_CENTS


def _total_tokens(usage: Any) -> int | None:
    """Pull a usable total-token count out of an OpenAI-style ``usage`` dict.

    Prefers ``total_tokens``; falls back to ``prompt_tokens + completion_tokens``
    when only the parts are present. Returns None when ``usage`` is not a dict or
    carries no numeric totals, so the caller records no cost rather than a wrong
    one. Tolerant of strings that parse as ints (some gateways stringify).
    """
    if not isinstance(usage, dict):
        return None

    def _as_int(value: Any) -> int | None:
        if isinstance(value, bool):  # bool is an int subclass; reject it.
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return None
        return None

    total = _as_int(usage.get("total_tokens"))
    if total is not None:
        return total
    prompt = _as_int(usage.get("prompt_tokens"))
    completion = _as_int(usage.get("completion_tokens"))
    if prompt is None and completion is None:
        return None
    return (prompt or 0) + (completion or 0)


def estimate_cost_cents(model: str | None, usage: Any) -> float | None:
    """Estimate a text turn's cost in US cents from a model id and a usage dict.

    ``usage`` is the OpenAI-compatible ``usage`` object the gateway returns
    (``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``). Returns None
    when ``usage`` lacks any usable token total, so a turn with no metering data
    records no cost rather than a fabricated one. Otherwise multiplies the total
    tokens by the model's blended per-1k price (a conservative ESTIMATE; see the
    price map) and returns cents, rounded to 4 decimals to match the ledger's
    Numeric(12, 4) column.

    This is an ESTIMATE to be reconciled against the gateway / Lago later: it
    blends prompt and completion at one rate and keys price by a model-name
    substring, so it is order-of-magnitude correct, not invoice-exact.
    """
    total = _total_tokens(usage)
    if total is None:
        return None
    price_per_1k = _price_per_1k_cents(model)
    return round((total / 1000.0) * price_per_1k, 4)


def validate_governance_update(
    *,
    agent_kind: str,
    autonomy: str | None,
    weekly_budget_cents: int | None,
) -> str | None:
    """Validate the fields of a governance write; return an error string or None.

    Pure validator the PUT route uses so the same rules are unit-testable without
    standing up FastAPI. Returns a human-readable message for the first problem
    (unknown ``agent_kind``, an ``autonomy`` outside ``AUTONOMY_LEVELS``, or a
    negative ``weekly_budget_cents``), or None when the update is acceptable. A
    None ``autonomy`` / ``weekly_budget_cents`` means "not being changed", which
    is always valid.
    """
    if agent_kind not in AGENT_KINDS:
        return (
            f"unknown agent_kind {agent_kind!r}; expected one of "
            f"{', '.join(AGENT_KINDS)}"
        )
    if autonomy is not None and autonomy not in AUTONOMY_LEVELS:
        return (
            f"unknown autonomy {autonomy!r}; expected one of "
            f"{', '.join(AUTONOMY_LEVELS)}"
        )
    if weekly_budget_cents is not None and weekly_budget_cents < 0:
        return "weekly_budget_cents must be zero or positive"
    return None
