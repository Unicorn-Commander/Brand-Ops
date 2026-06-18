"""The spoken-voice catalog for agent identities.

Brand-Ops owns how an agent looks and sounds; the suite voice plane (orator, on
the uc-1 box) and Brigade run it. This module is the "sounds" half: the curated
roster of TTS voices an agent can be assigned, plus a best-effort proxy that
synthesizes one short preview utterance through the configured orator service so
a person can hear a voice before binding it.

The roster below mirrors what orator actually serves today (two engines:
``pocket-tts`` premium customs and ``kokoro-onnx`` built-ins). It is the
authoritative OFFERED set, so the catalog works with no network and never blocks
on orator being up. Assigning a voice and exporting it in an agent manifest needs
nothing from orator; only the live preview does.

There is no SSRF surface here: a preview request carries a voice id and optional
text, never a URL, and the proxy only ever calls the one ``orator_base_url`` from
settings.
"""
from __future__ import annotations

from typing import Any

# The orator engines, kept as constants so the catalog and any consumer agree on
# the spelling that orator itself reports in /voices.
ENGINE_POCKET = "pocket-tts"
ENGINE_KOKORO = "kokoro-onnx"

# The curated voice roster, seeded from orator's live /voices, as compact rows of
# (id, label, engine, kind, descriptor). Premium pocket-tts customs first, then
# the kokoro built-ins grouped by accent and gender. ``label`` is the human name a
# picker shows; ``descriptor`` is a short accent/gender hint (kokoro's af/am/bf/bm
# prefixes encode American/British x female/male reliably); ``kind`` is "custom"
# (premium) or "built-in".
_K = ENGINE_KOKORO
_P = ENGINE_POCKET
_ROWS: tuple[tuple[str, str, str, str, str], ...] = (
    # pocket-tts premium customs.
    ("alba", "Alba", _P, "custom", "Premium voice"),
    ("cosette", "Cosette", _P, "custom", "Premium female"),
    ("eponine", "Eponine", _P, "custom", "Premium female"),
    ("fantine", "Fantine", _P, "custom", "Premium female"),
    ("azelma", "Azelma", _P, "custom", "Premium female"),
    ("javert", "Javert", _P, "custom", "Premium male"),
    ("jean", "Jean", _P, "custom", "Premium male"),
    ("marius", "Marius", _P, "custom", "Premium male"),
    # kokoro built-ins: American female.
    ("af", "Aria", _K, "built-in", "American female"),
    ("af_heart", "Heart", _K, "built-in", "American female"),
    ("af_bella", "Bella", _K, "built-in", "American female"),
    ("af_nicole", "Nicole", _K, "built-in", "American female"),
    ("af_sarah", "Sarah", _K, "built-in", "American female"),
    ("af_sky", "Sky", _K, "built-in", "American female"),
    ("af_nova", "Nova", _K, "built-in", "American female"),
    ("af_river", "River", _K, "built-in", "American female"),
    ("af_alloy", "Alloy", _K, "built-in", "American female"),
    # kokoro built-ins: American male.
    ("am_adam", "Adam", _K, "built-in", "American male"),
    ("am_michael", "Michael", _K, "built-in", "American male"),
    ("am_echo", "Echo", _K, "built-in", "American male"),
    ("am_onyx", "Onyx", _K, "built-in", "American male"),
    ("am_puck", "Puck", _K, "built-in", "American male"),
    # kokoro built-ins: British.
    ("bf_emma", "Emma", _K, "built-in", "British female"),
    ("bf_isabella", "Isabella", _K, "built-in", "British female"),
    ("bm_george", "George", _K, "built-in", "British male"),
    ("bm_lewis", "Lewis", _K, "built-in", "British male"),
)

_VOICES: tuple[dict[str, str], ...] = tuple(
    {"id": r[0], "label": r[1], "engine": r[2], "kind": r[3], "descriptor": r[4]}
    for r in _ROWS
)

_BY_ID: dict[str, dict[str, str]] = {v["id"]: v for v in _VOICES}

# The default preview line when a caller supplies none. Short so a preview is
# quick and cheap; brand-neutral so it reads for any agent.
DEFAULT_PREVIEW_TEXT = "Hi, I'm your brand agent. This is how I sound."
# A hard cap on preview text so a preview stays a preview (and the proxy stays
# cheap), independent of the orator service's own limits.
_PREVIEW_TEXT_MAX = 300
# Refuse an oversized audio body rather than buffer it unbounded.
_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
# TTS takes a beat; allow for it but never hang a request open.
_PREVIEW_TIMEOUT_S = 25.0


def list_voices() -> list[dict[str, str]]:
    """The curated voice roster a picker shows, in display order."""
    return [dict(v) for v in _VOICES]


def is_known(voice_id: str | None) -> bool:
    """Whether ``voice_id`` is one of the curated voices."""
    return bool(voice_id) and voice_id in _BY_ID


def engine_for(voice_id: str | None) -> str | None:
    """The orator engine that owns ``voice_id``, or None if unknown."""
    v = _BY_ID.get(voice_id or "")
    return v["engine"] if v else None


def preview_available(settings: Any | None = None) -> bool:
    """Whether live preview is wired (an orator base url is configured)."""
    from .config import get_settings

    settings = settings or get_settings()
    return bool(getattr(settings, "orator_base_url", ""))


class VoicePreviewError(Exception):
    """A preview could not be produced (orator unset, unreachable, or refused)."""


def synthesize_preview(
    voice_id: str,
    text: str | None = None,
    speed: float = 1.0,
    settings: Any | None = None,
) -> tuple[bytes, str]:
    """Synthesize one short utterance in ``voice_id`` through orator.

    Returns ``(audio_bytes, content_type)``. Raises ``VoicePreviewError`` when no
    orator is configured, the voice is unknown, or orator is unreachable or
    refuses, so the route maps it to a clean status rather than a 500. The call
    only ever targets the single configured ``orator_base_url`` (no caller-supplied
    host), posts one capped utterance, and refuses an oversized response body.
    """
    import httpx

    from .config import get_settings

    settings = settings or get_settings()
    base = (getattr(settings, "orator_base_url", "") or "").rstrip("/")
    if not base:
        raise VoicePreviewError("voice preview is not configured")
    if not is_known(voice_id):
        raise VoicePreviewError(f"unknown voice {voice_id!r}")

    body = (text or DEFAULT_PREVIEW_TEXT).strip()[:_PREVIEW_TEXT_MAX]
    try:
        spd = float(speed)
    except (TypeError, ValueError):
        spd = 1.0
    spd = min(max(spd, 0.5), 2.0)

    try:
        with httpx.Client(timeout=_PREVIEW_TIMEOUT_S) as client:
            resp = client.post(
                f"{base}/v1/audio/speech",
                json={"text": body, "voice": voice_id, "speed": spd, "stream": False},
            )
            resp.raise_for_status()
            audio = resp.content
            content_type = resp.headers.get("content-type", "audio/wav")
    except httpx.HTTPError as exc:
        raise VoicePreviewError(f"orator unreachable: {exc}") from None
    if not audio:
        raise VoicePreviewError("orator returned no audio")
    if len(audio) > _PREVIEW_MAX_BYTES:
        raise VoicePreviewError("preview audio too large")
    return audio, content_type
