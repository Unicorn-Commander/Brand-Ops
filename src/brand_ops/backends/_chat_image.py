"""Shared chat/completions image-generation protocol.

The OpenAI-style ``/chat/completions`` endpoint can drive image models (Gemini /
nano-banana / Flux) with reference images carried as ``image_url`` content parts,
and returns the generated image(s) under ``choices[0].message.images[]``. Two
Brand-Ops backends speak this exact protocol:

* ``openrouter`` -- direct, with a caller-supplied OpenRouter key (vacuum deploy).
* ``litellm``    -- the suite gateway, which now relays reference images through
  to the same image models and meters the spend (the integrated deploy, where
  Brand-Ops holds no provider keys of its own).

So the request-building (content, sizing, body) and the response decoding live
here once, and each backend supplies only its own transport: its credentials, its
endpoint path, and its own SSRF-gated ``_fetch`` (kept in the backend module so
the security tests can patch the module's ``httpx``). Nothing here touches the
network; the only httpx-bearing step, fetching a non-data result URL, is passed
in as a callable so the gate stays in the backend.
"""
from __future__ import annotations

import base64
import random
import time
from pathlib import Path

import httpx

from .base import BackendError, GenRequest

# Default to nano-banana 2 (Pro-level quality at Flash speed and cost). Both the
# openrouter backend and the gateway register this OpenRouter slug.
DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"

# OpenRouter image_size buckets. 1K is the documented default.
_VALID_SIZES = ("0.5K", "1K", "2K", "4K")

# Standard aspect ratios mapped to pixel dimensions; the requested width:height is
# snapped to the nearest of these.
_ASPECT_RATIOS: tuple[tuple[str, float], ...] = (
    ("1:1", 1.0),
    ("16:9", 16 / 9),
    ("9:16", 9 / 16),
    ("4:3", 4 / 3),
    ("3:4", 3 / 4),
    ("3:2", 3 / 2),
    ("2:3", 2 / 3),
    ("21:9", 21 / 9),
)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def data_url(path: Path) -> str:
    """A base64 ``data:`` URL for a reference image, mime keyed off the suffix."""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise BackendError(f"could not read reference image {path}: {exc}") from exc
    mime = _MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_content(req: GenRequest):
    """Plain string for text-to-image; a content array when editing with refs.

    The content array (text part + one image_url part per reference) is what
    carries the existing brand art to the image model for a style match.
    """
    if not req.reference_images:
        return req.prompt
    parts: list[dict] = [{"type": "text", "text": req.prompt}]
    for path in req.reference_images:
        parts.append({"type": "image_url", "image_url": {"url": data_url(path)}})
    return parts


def aspect_ratio(req: GenRequest) -> str:
    """Snap the requested dimensions to the nearest supported aspect ratio."""
    if req.width <= 0 or req.height <= 0:
        return "1:1"
    target = req.width / req.height
    return min(_ASPECT_RATIOS, key=lambda ar: abs(ar[1] - target))[0]


def image_size(req: GenRequest) -> str:
    """Honor an explicit hint, else derive a tier from the larger dimension."""
    if req.resolution_hint:
        hint = req.resolution_hint.strip().upper()
        if hint in _VALID_SIZES:
            return hint
        raise BackendError(
            f"invalid resolution_hint {req.resolution_hint!r}; expected one of "
            f"{', '.join(_VALID_SIZES)}"
        )
    longest = max(req.width, req.height)
    if longest >= 3000:
        return "4K"
    if longest >= 1500:
        return "2K"
    return "1K"


def build_body(model: str, req: GenRequest, *, modalities=None, user=None) -> dict:
    """Assemble the /chat/completions request body for one image generation.

    ``modalities`` falls back to the dual ["image", "text"] that the Gemini / GPT
    image models expect. ``user`` is the OpenAI-compatible attribution field: the
    gateway uses it to meter the spend per workspace; it is omitted when empty and
    is never a secret (an opaque workspace uuid).
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": build_content(req)}],
        "modalities": list(modalities) if modalities else ["image", "text"],
        "image_config": {
            "aspect_ratio": aspect_ratio(req),
            "image_size": image_size(req),
        },
        "stream": False,
    }
    if user:
        body["user"] = str(user)
    return body


def decode_data_url(url: str) -> tuple[bytes | None, str | None]:
    """Decode a ``data:<mime>;base64,<payload>`` URL to (bytes, mime)."""
    if not url.startswith("data:"):
        return None, None
    header, _, payload = url.partition(",")
    if not payload:
        return None, None
    meta = header[len("data:") :]
    mime = (meta.split(";", 1)[0] if ";" in meta else meta) or "image/png"
    try:
        return base64.b64decode(payload), mime
    except ValueError:
        # binascii.Error subclasses ValueError; a malformed payload is just skipped.
        return None, None


def extract_chat_images(data: dict, fetch) -> tuple[list[bytes], str | None]:
    """Pull image bytes from ``choices[0].message.images[]``.

    Each item is either an inline ``data:`` URL (decoded here) or a remote URL
    (handed to ``fetch``, the backend's own SSRF-gated fetcher, so a compromised
    upstream cannot aim the fetch at an internal service). ``fetch`` returns the
    bytes or ``None``; this stays network-free so it never needs the backends'
    httpx patched.
    """
    choices = data.get("choices") or []
    if not choices:
        return [], None
    message = (choices[0] or {}).get("message") or {}
    out: list[bytes] = []
    mime: str | None = None
    for item in message.get("images") or []:
        url = ((item or {}).get("image_url") or {}).get("url")
        if not url:
            continue
        if url.startswith("data:"):
            decoded, item_mime = decode_data_url(url)
            if decoded:
                out.append(decoded)
                mime = item_mime or mime
            continue
        fetched = fetch(url)
        if fetched:
            out.append(fetched)
    return out, mime


def usage_cost_cents(data: dict) -> float | None:
    """The gateway's reported spend for this call, in cents, when present.

    The gateway returns ``usage.cost`` in dollars; surfacing it as cents lets the
    per-tenant metering record the real charge instead of a static estimate.
    """
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)):
        return round(float(cost) * 100, 4)
    return None


# --- bounded retry around upstream POSTs --------------------------------------
#
# A single transient upstream blip (a 429 burst or a brief 5xx / connect timeout)
# should not hard-fail a user-facing generation. post_with_retry adds a small,
# bounded retry with exponential backoff and jitter around one POST. Permanent
# failures (other 4xx, or a still-failing response after the last attempt) are
# returned to the caller unchanged, so each backend keeps raising BackendError
# exactly as before.

# At most three POSTs total per generation call.
_MAX_ATTEMPTS = 3
# Statuses worth retrying: rate limit and the transient gateway/server 5xx set.
# Other 4xx (400, 401, 403, ...) are permanent and never retried.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Base backoff in seconds; attempt N waits roughly _BASE_DELAY_S * 2**(N-1) plus
# jitter. Small by default so production recovers fast; tests set it to 0 (and/or
# patch _SLEEP) so the retry path runs without real waiting.
_BASE_DELAY_S = 0.5
# Never honor a Retry-After larger than this; a hostile or buggy upstream could
# otherwise pin us near the request timeout.
_MAX_RETRY_AFTER_S = 30.0
# Indirection so a test can patch the sleep to a no-op and stay fast.
_SLEEP = time.sleep


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a Retry-After header (delta-seconds only), capped, else None.

    Only the integer delta-seconds form is honored; the HTTP-date form is rare
    here and is treated as absent so a malformed value never blocks the backoff.
    """
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        secs = float(raw.strip())
    except ValueError:
        return None
    if secs < 0:
        return None
    return min(secs, _MAX_RETRY_AFTER_S)


def _backoff_delay(attempt: int, response: httpx.Response | None) -> float:
    """Seconds to wait before the next attempt (1-indexed current attempt).

    Honors a server Retry-After when present, else exponential backoff with a
    half-window of deterministic jitter (seeded by attempt) to spread retries.
    """
    if response is not None:
        hinted = _retry_after_seconds(response)
        if hinted is not None:
            return hinted
    base = _BASE_DELAY_S * (2 ** (attempt - 1))
    # Deterministic, attempt-seeded jitter keeps tests stable while still
    # de-synchronizing concurrent callers in production.
    jitter = random.Random(attempt).uniform(0.0, base * 0.5)
    return base + jitter


def post_with_retry(client: httpx.Client, url: str, *, json: dict, deadline: float):
    """POST with a bounded retry on transient failures; return the response.

    Retries up to ``_MAX_ATTEMPTS`` total on httpx connect/timeout errors and on
    the ``_RETRY_STATUSES`` set, with exponential backoff, jitter, and Retry-After
    support. ``deadline`` is a ``time.monotonic`` instant past which no further
    sleep or attempt is started, so the whole loop stays under the caller's
    timeout. A non-retryable response (e.g. 400) is returned on the first try, and
    a still-failing response after the last attempt is returned as-is so the
    caller raises BackendError exactly as today. The transient connect/timeout
    error from the final attempt is re-raised for the caller's existing handler.
    """
    last_exc: httpx.TransportError | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None
        try:
            response = client.post(url, json=json)
        except httpx.TransportError as exc:
            # Connect errors and timeouts are transient; remember and maybe retry.
            last_exc = exc
        else:
            if response.status_code not in _RETRY_STATUSES:
                return response

        if attempt == _MAX_ATTEMPTS:
            break
        delay = _backoff_delay(attempt, response)
        # Do not sleep (or start another attempt) past the caller's deadline.
        if time.monotonic() + delay >= deadline:
            break
        _SLEEP(delay)

    if response is not None:
        # A retryable status that never cleared; let the caller raise as today.
        return response
    # Only transient transport errors were seen; re-raise the last one.
    raise last_exc
