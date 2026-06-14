"""Tests for real brand intake: the agent, the writer, the REST route, the MCP.

No test here touches a real LLM or the network. The agent is exercised through a
stub Brigade client (the same offline seam every other agent uses) and the route
is hit with the in-process TestClient while the LLM chat path is either stubbed
or left to report no gateway. The writer is exercised against a throwaway copy of
the repo brand-kits (a tmp ``kits_dir``) so the real kit set is never touched.

The REST and MCP surfaces resolve against ``DEFAULT_KITS_DIR`` (they take no
kits_dir), so those tests register a cleanup that removes any brand directory
they create. The repo brand-kits is left exactly as it was found; the suite
asserts that at the end.

What is proven:
  1. A good JSON draft from the model creates a brand whose colors match the
     draft and the endpoint returns the full BrandDetail shape.
  2. With ``parent`` set the new brand records the parent.
  3. A no-gateway stub still creates a brand from the neutral default plus a
     note (never 500).
  4. A duplicate slug is a 409; a name that yields no valid slug is a 422.
"""
from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from brand_ops import mcp_server
from brand_ops.agents.brand_intake import BrandIntakeAgent
from brand_ops.api.app import create_app
from brand_ops.api.schemas import BrandDetail, IntakeResult
from brand_ops.brand_clone import create_brand_from_draft
from brand_ops.brand_loader import DEFAULT_KITS_DIR
from brand_ops.config import Settings

# The parent brand the consistency tests seed from, and its known primary.
PARENT = "magic-unicorn"
PARENT_PRIMARY = "#b66eff"

# A good, complete identity draft the model is stubbed to return. Valid hex
# everywhere, three fonts, lists, and clean copy.
GOOD_DRAFT = {
    "palette": {
        "primary": "#ff5a36",
        "accent": "#1fb6ff",
        "background": "#0b0b0f",
        "foreground": "#f5f5f7",
        "muted": "#8a8a93",
    },
    "typography": {
        "primary_font": "Satoshi",
        "display_font": "Clash Display",
        "mono_font": "Berkeley Mono",
    },
    "voice_tone": ["warm", "precise", "playful"],
    "style_keywords": ["soft gradients", "rounded forms", "high contrast", "airy"],
    "tagline": "Tools that get out of your way",
    "description": (
        "Acme builds focused software for small teams. It keeps the surface "
        "calm and the work fast."
    ),
    "rationale": (
        "A warm primary with a cool accent reads friendly but capable; the dark "
        "surface keeps it modern."
    ),
}


class _StubBrigade:
    """A stand-in Brigade client. Returns a fixed chat reply; never hits network.

    Mirrors the stub in tests/test_director.py so the intake agent is exercised
    through the same offline seam every other agent uses.
    """

    def __init__(self, chat_reply: str | None = None) -> None:
        self.chat_reply = chat_reply
        self.chat_calls: list[dict[str, Any]] = []

    async def research(self, topic: str, depth: str = "medium") -> dict[str, Any]:
        return {"summary": "stub research"}

    async def llm_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        *,
        temperature: float = 0.4,
        response_json: bool = False,
    ) -> str | None:
        self.chat_calls.append(
            {"messages": messages, "model": model, "response_json": response_json}
        )
        return self.chat_reply


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ─────────────────────────── Fixtures ───────────────────────────────────────


@pytest.fixture
def kits(tmp_path: Path) -> Path:
    """A throwaway copy of the repo brand-kits the writer writes into."""
    dest = tmp_path / "brand-kits"
    shutil.copytree(DEFAULT_KITS_DIR, dest)
    return dest


@pytest.fixture
def cleanup_real_kits() -> Iterator[list[str]]:
    """Track brand slugs created in the *real* brand-kits and remove them after.

    The REST and MCP surfaces resolve against DEFAULT_KITS_DIR, so an intake
    there lands in the repo. A test appends each new slug to the yielded list;
    the fixture deletes those directories on teardown so no stray test brand
    remains. Mirrors tests/test_clone.py.
    """
    created: list[str] = []
    try:
        yield created
    finally:
        for slug in created:
            shutil.rmtree(DEFAULT_KITS_DIR / slug, ignore_errors=True)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Auth off and no LLM gateway by default, made explicit regardless of ambient
    # env so a route test that does not stub chat takes the deterministic
    # no-gateway path and never reaches a live model.
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return TestClient(create_app())


# ─────────────────────────── Agent: draft_identity ──────────────────────────


def test_agent_drafts_from_good_json_with_valid_palette() -> None:
    agent = BrandIntakeAgent(
        brigade=_StubBrigade(chat_reply=json.dumps(GOOD_DRAFT)), settings=Settings()
    )
    draft, notes = _run(agent.draft_identity("Acme", description="focused software"))
    # The drafted palette is the model's, validated.
    assert draft["palette"]["primary"] == "#ff5a36"
    assert draft["palette"]["accent"] == "#1fb6ff"
    assert draft["typography"]["primary_font"] == "Satoshi"
    assert draft["voice_tone"] == ["warm", "precise", "playful"]
    assert draft["tagline"] == "Tools that get out of your way"
    # The agent asked the gateway for JSON.
    assert agent.brigade.chat_calls[-1]["response_json"] is True
    # A good draft with a description carries no default note.
    assert not any("starting point" in n.lower() for n in notes)


def test_agent_invalid_hex_backfilled_from_default_with_note() -> None:
    bad = dict(GOOD_DRAFT)
    bad["palette"] = {
        "primary": "not-a-color",
        "accent": "#1fb6ff",
        "background": "#0b0b0f",
        "foreground": "#f5f5f7",
        "muted": "#8a8a93",
    }
    agent = BrandIntakeAgent(
        brigade=_StubBrigade(chat_reply=json.dumps(bad)), settings=Settings()
    )
    draft, notes = _run(agent.draft_identity("Acme", description="x"))
    # The bad primary was replaced by a valid default hex; the good ones kept.
    assert draft["palette"]["primary"].startswith("#")
    assert draft["palette"]["primary"] != "not-a-color"
    assert draft["palette"]["accent"] == "#1fb6ff"
    assert any("primary" in n for n in notes)


def test_agent_scrubs_em_dash_and_hype_from_copy() -> None:
    dirty = dict(GOOD_DRAFT)
    dirty["tagline"] = "Revolutionary tools — built to elevate your team"
    agent = BrandIntakeAgent(
        brigade=_StubBrigade(chat_reply=json.dumps(dirty)), settings=Settings()
    )
    draft, _ = _run(agent.draft_identity("Acme", description="x"))
    tagline = draft["tagline"]
    assert "—" not in tagline
    assert "revolutionary" not in tagline.lower()
    assert "elevate" not in tagline.lower()


def test_agent_no_gateway_yields_default_draft_with_note() -> None:
    agent = BrandIntakeAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    draft, notes = _run(agent.draft_identity("Acme", description="x"))
    # A complete, valid identity even with no model.
    assert draft["palette"]["primary"].startswith("#")
    assert draft["palette"]["background"] == "#0a0a0a"
    assert draft["typography"]["primary_font"]
    assert draft["voice_tone"]
    # And an honest starting-point note.
    assert any("starting point" in n.lower() for n in notes)


def test_agent_parent_consistency_default_echoes_parent_footing() -> None:
    # With a parent and no model, the default palette should echo the parent's
    # footing (its background/foreground/primary), so a child still reads family.
    parent_identity = {
        "slug": PARENT,
        "display_name": "Magic Unicorn",
        "colors": {
            "primary": PARENT_PRIMARY,
            "accent": "#00d4ff",
            "background": "#0a0a0a",
            "foreground": "#ffffff",
            "muted": "#b3b3b3",
        },
        "voice": {"tone": ["bold", "confident"]},
        "style_keywords": ["dark UI", "neon glow"],
    }
    agent = BrandIntakeAgent(brigade=_StubBrigade(chat_reply=None), settings=Settings())
    draft, _ = _run(
        agent.draft_identity("Magic Unicorn Labs", parent_brand=parent_identity)
    )
    assert draft["palette"]["primary"] == PARENT_PRIMARY
    assert draft["palette"]["background"] == "#0a0a0a"


def test_agent_onboard_mode_notes_unreachable_site() -> None:
    # Onboard mode now actually fetches the live site (no Brigade required). A
    # reserved .example domain never resolves, so the fetcher returns None and the
    # agent degrades honestly: it says it could not read the site and drafted from
    # the name + domain as hints. It no longer claims it needs Brigade to read a
    # page (that framing is gone; Brigade is only a future deep-research add-on).
    agent = BrandIntakeAgent(
        brigade=_StubBrigade(chat_reply=json.dumps(GOOD_DRAFT)), settings=Settings()
    )
    _, notes = _run(
        agent.draft_identity("Acme", url="https://acme.example", mode="onboard")
    )
    assert any("live site" in n.lower() for n in notes)
    assert not any("Brigade" in n for n in notes)


# ─────────────────────────── Writer: create_brand_from_draft ────────────────


def test_writer_creates_complete_valid_brand(kits: Path) -> None:
    brand = create_brand_from_draft("acme-co", GOOD_DRAFT, kits_dir=kits)
    assert brand.brand.slug == "acme-co"
    assert brand.visual.colors.primary == "#ff5a36"
    assert brand.visual.typography.primary_font == "Satoshi"
    assert brand.voice.tone == ["warm", "precise", "playful"]
    assert brand.brand.parent is None
    # No mark on a fresh brand, and no reference images.
    assert brand.brand.mark is None
    assert brand.reference_images == {}
    # Default prompts are written so it is immediately generatable.
    assert "logo" in brand.prompts and "og_image" in brand.prompts

    # The written file carries the colors itself (not inherited) and a tagline.
    raw = yaml.safe_load((kits / "acme-co" / "brand.yaml").read_text())
    assert raw["visual"]["colors"]["primary"] == "#ff5a36"
    assert raw["brand"]["tagline"] == "Tools that get out of your way"
    assert raw["reference_images"] == {}
    assert "mark" not in raw["brand"]


def test_writer_records_parent(kits: Path) -> None:
    brand = create_brand_from_draft(
        "mu-labs", GOOD_DRAFT, parent=PARENT, kits_dir=kits
    )
    assert brand.brand.parent == PARENT


def test_writer_bad_slug_raises(kits: Path) -> None:
    with pytest.raises(ValueError):
        create_brand_from_draft("Bad Slug!", GOOD_DRAFT, kits_dir=kits)
    assert not (kits / "Bad Slug!").exists()


def test_writer_duplicate_slug_raises(kits: Path) -> None:
    create_brand_from_draft("acme-dup", GOOD_DRAFT, kits_dir=kits)
    with pytest.raises(FileExistsError):
        create_brand_from_draft("acme-dup", GOOD_DRAFT, kits_dir=kits)


def test_writer_invalid_hex_in_draft_raises_value_error(kits: Path) -> None:
    # The writer trusts the agent to validate hex, but if a raw draft with a bad
    # color reaches it, the validating load surfaces a ValueError (a clean 422 at
    # the route), not a silently broken file.
    bad = dict(GOOD_DRAFT)
    bad["palette"] = {**GOOD_DRAFT["palette"], "primary": "purple"}
    with pytest.raises(ValueError):
        create_brand_from_draft("acme-badhex", bad, kits_dir=kits)


# ─────────────────────────── REST: POST /api/brands/intake ──────────────────


def test_route_good_draft_creates_brand_and_returns_branddetail(
    client: TestClient, cleanup_real_kits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_real_kits.append("acme-rest")

    async def _fake_chat(self: BrandIntakeAgent, *args: Any, **kwargs: Any) -> str:
        return json.dumps(GOOD_DRAFT)

    monkeypatch.setattr(BrandIntakeAgent, "chat", _fake_chat)

    resp = client.post(
        "/api/brands/intake",
        json={"name": "Acme Rest", "description": "focused software"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # The whole envelope validates against the wire contract.
    result = IntakeResult.model_validate(body)
    # The new brand is the full BrandDetail shape, with the drafted colors.
    assert result.brand.slug == "acme-rest"
    assert result.brand.display_name == "Acme Rest"
    assert result.brand.visual.colors.primary == GOOD_DRAFT["palette"]["primary"]
    assert result.brand.mark_url is None
    # The draft is echoed for the conversation to narrate.
    assert result.draft.palette.primary == GOOD_DRAFT["palette"]["primary"]
    assert result.draft.tagline == GOOD_DRAFT["tagline"]
    assert result.draft.rationale

    # And it is a real, fetchable brand at the same colors.
    follow = client.get("/api/brands/acme-rest")
    assert follow.status_code == 200
    assert follow.json()["visual"]["colors"]["primary"] == GOOD_DRAFT["palette"]["primary"]
    # The BrandDetail in the envelope matches the standalone GET.
    assert BrandDetail.model_validate(follow.json()).slug == result.brand.slug


def test_route_with_parent_sets_parent_and_stays_consistent(
    client: TestClient, cleanup_real_kits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cleanup_real_kits.append("acme-child-rest")

    # A draft that picks up the parent's primary to show consistency flowing
    # through; the agent is given the parent identity as context regardless.
    child_draft = dict(GOOD_DRAFT)
    child_draft["palette"] = {**GOOD_DRAFT["palette"], "primary": PARENT_PRIMARY}

    async def _fake_chat(self: BrandIntakeAgent, *args: Any, **kwargs: Any) -> str:
        return json.dumps(child_draft)

    monkeypatch.setattr(BrandIntakeAgent, "chat", _fake_chat)

    resp = client.post(
        "/api/brands/intake",
        json={"name": "Acme Child Rest", "parent": PARENT},
    )
    assert resp.status_code == 200
    result = IntakeResult.model_validate(resp.json())
    # The new brand records the parent.
    assert result.brand.parent == PARENT
    assert result.brand.visual.colors.primary == PARENT_PRIMARY


def test_route_no_gateway_still_creates_brand_with_note(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    # The client fixture cleared the LLM env, so the shared gateway reports no
    # gateway and the agent's chat returns None. The brand is still created from
    # the neutral default, with an honest note, and the route is 200 not 500.
    cleanup_real_kits.append("acme-nolm")
    resp = client.post(
        "/api/brands/intake",
        json={"name": "Acme Nolm", "description": "anything"},
    )
    assert resp.status_code == 200
    result = IntakeResult.model_validate(resp.json())
    assert result.brand.slug == "acme-nolm"
    # A complete, valid default identity.
    assert result.brand.visual.colors.primary.startswith("#")
    assert result.draft.palette.background == "#0a0a0a"
    # An honest note that the identity is a starting point.
    assert result.notes
    assert any("starting point" in n.lower() for n in result.notes)


def test_route_duplicate_slug_is_409(
    client: TestClient, cleanup_real_kits: list[str]
) -> None:
    cleanup_real_kits.append("acme-dup-rest")
    first = client.post("/api/brands/intake", json={"name": "Acme Dup Rest"})
    assert first.status_code == 200
    again = client.post("/api/brands/intake", json={"name": "Acme Dup Rest"})
    assert again.status_code == 409


def test_route_bad_name_is_422(client: TestClient) -> None:
    # A name with no letter or digit yields no usable slug: a clean 422, and no
    # directory is created.
    resp = client.post("/api/brands/intake", json={"name": "!!! ---"})
    assert resp.status_code == 422
    # The slug _slugify would have fallen back to is "brand"; assert it was not
    # written, proving the guard fired before any file work.
    assert not (DEFAULT_KITS_DIR / "brand").exists()


# ─────────────────────────── MCP: intake_brand tool ─────────────────────────


def test_mcp_intake_brand_no_gateway_creates_brand(
    cleanup_real_kits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Clear the gateway so the tool takes the deterministic default path.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cleanup_real_kits.append("acme-mcp")
    result = mcp_server.intake_brand("Acme MCP", description="anything")
    assert "error" not in result
    assert result["brand"]["slug"] == "acme-mcp"
    assert result["draft"]["palette"]["primary"].startswith("#")
    assert result["notes"]


def test_mcp_intake_brand_duplicate_returns_error(
    cleanup_real_kits: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cleanup_real_kits.append("acme-dup-mcp")
    first = mcp_server.intake_brand("Acme Dup MCP")
    assert "error" not in first
    again = mcp_server.intake_brand("Acme Dup MCP")
    assert "error" in again


def test_mcp_intake_brand_bad_name_returns_error() -> None:
    result = mcp_server.intake_brand("!!! ---")
    assert "error" in result
    assert not (DEFAULT_KITS_DIR / "brand").exists()


# ─────────────────────────── Cleanliness guard ──────────────────────────────


def test_brand_kits_is_clean_of_test_brands() -> None:
    # A backstop run after the suite: none of the test slugs should remain in the
    # repo brand-kits. The cleanup fixtures remove them per test; this asserts the
    # repo is left exactly as found even if a test forgot to register one.
    leftovers = [
        slug
        for slug in (
            "acme-rest",
            "acme-child-rest",
            "acme-nolm",
            "acme-dup-rest",
            "acme-mcp",
            "acme-dup-mcp",
            "brand",
        )
        if (DEFAULT_KITS_DIR / slug).exists()
    ]
    assert leftovers == [], f"stray test brands left in brand-kits: {leftovers}"
