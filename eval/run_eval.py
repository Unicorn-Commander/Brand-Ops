"""Qwen-vs-Claude eval for the Brand-Ops agents.

Four tasks mirror the real agent roles (brand-profile extraction, asset-pack
planning, prompt composition, on-brand critique). Each runs against two models
through the same OpenAI-compatible gateway the agents use, is scored by a
deterministic check, and the script prints a per-agent recommendation: default
each agent to the cheapest model that clears the bar.

Run (one OpenRouter key covers both models):
  export OPENROUTER_API_KEY=...        # or LLM_API_KEY
  uv run python eval/run_eval.py

The local Qwen 3.6 (midboy1) is not reachable from a laptop, so by default this
uses an OpenRouter Qwen-class model as a stand-in. Override the ids, and confirm
the exact local model at deploy when the cluster gateway is reachable:
  QWEN_MODEL=qwen/qwen3-235b-a22b CLAUDE_MODEL=anthropic/claude-3.7-sonnet \
    uv run python eval/run_eval.py
"""
from __future__ import annotations

import asyncio
import json
import os

from brand_ops.brigade.client import BrigadeClient
from brand_ops.config import get_settings
from brand_ops.lint import check_copy

RUNS = int(os.getenv("EVAL_RUNS", "2"))
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen/qwen3-235b-a22b")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "anthropic/claude-3.7-sonnet")
MODELS = [("qwen", QWEN_MODEL), ("claude", CLAUDE_MODEL)]

VALID_ASSETS = {"logo", "app_icon", "og_image", "podcast_cover", "social_card", "avatar"}
VALID_BACKENDS = {"gemini", "openai", "recraft", "flux"}
GEMINI_ASSETS = {"og_image", "social_card", "podcast_cover", "avatar"}
ALPHA_ASSETS = {"logo", "app_icon"}


def extract_json(text: str | None):
    """Pull the first JSON value out of a model response, tolerating code fences.

    Tries a full parse first, then an array slice, then an object slice, so a
    bare or single-item array is not mistaken for its inner object.
    """
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t.split("\n", 1)[-1] if "\n" in t else t
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i : j + 1])
            except json.JSONDecodeError:
                continue
    return None


def score_profile(text: str | None) -> tuple[float, str]:
    obj = extract_json(text)
    if not isinstance(obj, dict):
        return 0.0, "not a JSON object"
    need = ["display_name", "tagline", "palette", "voice", "style_keywords"]
    have = sum(1 for k in need if k in obj)
    return have / len(need), f"{have}/{len(need)} fields"


def score_plan(text: str | None) -> tuple[float, str]:
    obj = extract_json(text)
    items = obj if isinstance(obj, list) else (obj.get("items") if isinstance(obj, dict) else None)
    if not isinstance(items, list) or not items:
        return 0.0, "no item list"
    good = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        at, be = it.get("asset_type"), it.get("backend")
        ok = at in VALID_ASSETS and be in VALID_BACKENDS
        if ok and at in GEMINI_ASSETS:
            ok = be == "gemini"
        elif ok and at in ALPHA_ASSETS:
            ok = be in {"openai", "recraft"}
        good += 1 if ok else 0
    return good / len(items), f"{good}/{len(items)} sound"


def score_prompt(text: str | None) -> tuple[float, str]:
    if not text or not text.strip():
        return 0.0, "empty"
    issues = check_copy(text)
    if not issues:
        return 1.0, "clean copy"
    return max(0.0, 1.0 - 0.34 * len(issues)), f"{len(issues)} copy issue(s)"


def score_critique(text: str | None) -> tuple[float, str]:
    obj = extract_json(text)
    if not isinstance(obj, dict):
        return 0.0, "not a JSON object"
    score = 0.0
    if obj.get("on_brand") is False:
        score += 0.6
    if isinstance(obj.get("issues"), list) and obj.get("issues"):
        score += 0.2
    if isinstance(obj.get("fix"), str) and obj.get("fix").strip():
        score += 0.2
    return score, f"on_brand={obj.get('on_brand')}"


TASKS = [
    {
        "key": "brand_research",
        "agent": "BrandResearchAgent",
        "system": "You are a brand analyst. Output a strict JSON object only, no prose.",
        "user": (
            "Research notes: Tidewater Robotics builds rugged autonomous boats for coastal "
            "surveying. Founder-led, plain-spoken, marine and engineering heritage, deep navy "
            "and signal-orange site, mono spec sheets. Extract a brand profile as JSON with "
            "keys display_name, tagline, palette (primary and accent as hex), fonts, voice "
            "(list), style_keywords (list)."
        ),
        "scorer": score_profile,
    },
    {
        "key": "asset_pack",
        "agent": "AssetPackAgent",
        "system": "You plan brand asset packs. Output a strict JSON list only, no prose.",
        "user": (
            "Brand: Magic Unicorn, purple and cyan, vector aesthetic. Plan a launch pack as a "
            "JSON list of items, each with asset_type, variant, and backend. Asset types are "
            "logo, app_icon, og_image, podcast_cover, social_card, avatar. Backends are gemini, "
            "openai, recraft, flux. Guidance: logos and app icons need transparency or vector "
            "so use openai or recraft; og images, social cards, podcast covers, and avatars are "
            "text or composition heavy so use gemini."
        ),
        "scorer": score_plan,
    },
    {
        "key": "prompt_compose",
        "agent": "prompt composition",
        "system": (
            "You compose image-generation prompts. Hard rules: no em-dashes anywhere, and no "
            "marketing-speak (never use words like revolutionary, game-changer, world-class, "
            "cutting-edge, unleash). Stay on brand. Output only the prompt."
        ),
        "user": (
            "Brand: Center Deep, a deep-sea search and data-intelligence product. Palette deep "
            "blue, teal, and aqua, glassmorphic, soft light rays. Compose a prompt for a "
            "1200x630 og image announcing a product launch."
        ),
        "scorer": score_prompt,
    },
    {
        "key": "critique",
        "agent": "BrandCriticAgent",
        "system": "You are a brand critic. Output a strict JSON object only, no prose.",
        "user": (
            "Brand: MAJIKS, an independent music platform. Ethereal and mystical, dark mode, "
            "neon purple and gold, music-forward. A generated asset is described as: a flat "
            "corporate stock photo of two people shaking hands in a gray office, Helvetica "
            "caption, no music motifs, bright daylight. Judge it. Output JSON with keys "
            "on_brand (boolean), issues (list), fix (string)."
        ),
        "scorer": score_critique,
    },
]


async def run() -> int:
    settings = get_settings()
    client = BrigadeClient(settings)
    if not (settings.llm_base_url and settings.llm_api_key):
        print("Gateway not configured. Set OPENROUTER_API_KEY (or LLM_API_KEY) so the eval can")
        print("reach the models, then re-run. One OpenRouter key covers both Qwen and Claude.")
        print(f"  llm_base_url = {settings.llm_base_url or '(unset)'}")
        return 2

    print(f"Eval: {len(TASKS)} tasks x {len(MODELS)} models x {RUNS} runs")
    print(f"  qwen   = {QWEN_MODEL}")
    print(f"  claude = {CLAUDE_MODEL}\n")

    results = {t["key"]: {m: [] for m, _ in MODELS} for t in TASKS}
    any_response = False
    for t in TASKS:
        for label, model_id in MODELS:
            for _ in range(RUNS):
                messages = [
                    {"role": "system", "content": t["system"]},
                    {"role": "user", "content": t["user"]},
                ]
                text = await client.llm_chat(messages, model=model_id, temperature=0.3)
                if text is None:
                    results[t["key"]][label].append((0.0, "no response"))
                else:
                    any_response = True
                    results[t["key"]][label].append(t["scorer"](text))

    if not any_response:
        print("No model responded. Check the model ids (QWEN_MODEL / CLAUDE_MODEL) against the")
        print("OpenRouter catalog and that the key is valid.")
        return 1

    print(f"{'task':16} {'qwen':>8} {'claude':>8}  winner")
    print("-" * 48)
    winners: dict[str, str] = {}
    for t in TASKS:
        avgs = {}
        for label, _ in MODELS:
            scores = [s for s, _ in results[t["key"]][label]]
            avgs[label] = sum(scores) / len(scores) if scores else 0.0
        # prefer qwen (free, local) when it is within 0.1 and clears a 0.7 bar
        win = "claude"
        if avgs["qwen"] >= avgs["claude"] - 0.1 and avgs["qwen"] >= 0.7:
            win = "qwen"
        elif avgs["qwen"] > avgs["claude"]:
            win = "qwen"
        winners[t["agent"]] = win
        print(f"{t['key']:16} {avgs['qwen']:>8.2f} {avgs['claude']:>8.2f}  {win}")

    print("\nRecommended default model per agent (cheapest that clears the bar):")
    for agent, win in winners.items():
        print(f"  {agent:22} -> {win} ({QWEN_MODEL if win == 'qwen' else CLAUDE_MODEL})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
