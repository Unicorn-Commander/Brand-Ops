"""LiteLLM gateway image backend.

Calls the suite's LiteLLM gateway (the Ops-Center inference gateway) over its
OpenAI-compatible /v1/images/generations endpoint, authenticating with a
gateway-issued virtual key. The gateway holds the real provider credentials and
meters + marks up the spend, so when Brand-Ops runs integrated inside Unicorn
Commander it carries NO provider keys of its own. The native backends
(openrouter / gemini / openai / recraft / flux) remain only for standalone
"in a vacuum" deployments where someone brings their own keys.

Configured by settings.litellm_base_url + litellm_api_key + litellm_image_model.
httpx is a hard dependency; nothing here needs the optional `backends` extra.
"""
from __future__ import annotations

import base64

import httpx

from .base import BackendError, BackendNotConfigured, GenRequest, GenResult

# gpt-image-1 / dall-e accept a fixed set of sizes; we snap to the nearest by
# orientation. "auto" is also valid but we pick an explicit size for determinism.
_SQUARE = "1024x1024"
_LANDSCAPE = "1536x1024"
_PORTRAIT = "1024x1536"

_TIMEOUT_S = 180.0

# gpt-image-1 quality tiers; default medium for a sane cost/quality balance. A
# caller may override via req.extra["quality"]. Other models (e.g. dall-e-3) use
# a different quality vocabulary, so the param is only sent for gpt-image-1.
_VALID_QUALITIES = ("low", "medium", "high", "auto")
_DEFAULT_QUALITY = "medium"
# Rough per-image cost estimate in cents by quality (square output). Surfaced to
# callers for parity with the openai backend; the gateway does the real metering.
_COST_BY_QUALITY = {"low": 1.1, "medium": 4.0, "high": 17.0, "auto": 4.0}


class Backend:
    """LiteLLM gateway image backend adapter."""

    name = "litellm"

    def __init__(self, settings) -> None:
        self._settings = settings

    def health(self) -> bool:
        """True when the gateway URL, a key, and a model are all configured."""
        s = self._settings
        return bool(s.litellm_base_url and s.litellm_api_key and s.litellm_image_model)

    def generate(self, req: GenRequest) -> GenResult:
        s = self._settings
        if not self.health():
            raise BackendNotConfigured(
                "litellm gateway not configured "
                "(set LITELLM_BASE_URL, LITELLM_API_KEY, LITELLM_IMAGE_MODEL)"
            )
        model = req.extra.get("model") or s.litellm_image_model
        body = {
            "model": model,
            "prompt": req.prompt,
            "n": max(1, req.count),
            "size": self._size(req),
        }
        # gpt-image-1 honors a quality tier; default medium keeps cost sane (the
        # gateway otherwise bills the high tier). The gateway cannot return a
        # transparent background, so we do not ask for one here; the engine cuts
        # the background out after generation for transparent asset types.
        quality = None
        if "gpt-image" in model:
            quality = self._quality(req)
            body["quality"] = quality

        try:
            with self._client() as client:
                resp = client.post("/v1/images/generations", json=body)
        except httpx.HTTPError as exc:
            raise BackendError(f"litellm gateway request error: {exc}") from exc

        if resp.status_code != 200:
            raise BackendError(
                f"litellm gateway image request failed ({resp.status_code}): {resp.text[:300]}"
            )

        images, mime = self._extract_images(resp.json())
        if not images:
            raise BackendError("litellm gateway returned no image data in the response")

        return GenResult(
            images=images,
            mime=mime,
            backend=self.name,
            model=model,
            cost_cents=(self._cost_cents(quality) * len(images)) if quality else None,
            meta={"gateway": s.litellm_base_url, "size": body["size"], "quality": quality},
        )

    # --- internals -----------------------------------------------------------

    def _quality(self, req: GenRequest) -> str:
        """Honor an explicit req.extra['quality'], else default to medium."""
        raw = req.extra.get("quality")
        if raw is None:
            return _DEFAULT_QUALITY
        quality = str(raw).strip().lower()
        if quality not in _VALID_QUALITIES:
            valid = ", ".join(_VALID_QUALITIES)
            raise BackendError(f"invalid quality {raw!r}; expected one of {valid}")
        return quality

    def _cost_cents(self, quality: str) -> float:
        """Rough per-image cost estimate in cents, keyed by quality tier."""
        return _COST_BY_QUALITY.get(quality, _COST_BY_QUALITY[_DEFAULT_QUALITY])

    def _client(self) -> httpx.Client:
        """HTTP client for the gateway. Isolated so tests can swap the transport."""
        headers = {
            "Authorization": f"Bearer {self._settings.litellm_api_key}",
            "Content-Type": "application/json",
        }
        base = self._settings.litellm_base_url.rstrip("/")
        return httpx.Client(base_url=base, headers=headers, timeout=_TIMEOUT_S)

    def _size(self, req: GenRequest) -> str:
        if req.width <= 0 or req.height <= 0:
            return _SQUARE
        ratio = req.width / req.height
        if ratio >= 1.2:
            return _LANDSCAPE
        if ratio <= 0.83:
            return _PORTRAIT
        return _SQUARE

    def _extract_images(self, data: dict) -> tuple[list[bytes], str]:
        """Decode b64_json items (the OpenAI image shape), or fetch url items."""
        items = data.get("data") or []
        out: list[bytes] = []
        for item in items:
            b64 = (item or {}).get("b64_json")
            if b64:
                try:
                    out.append(base64.b64decode(b64))
                    continue
                except ValueError:
                    pass
            url = (item or {}).get("url")
            if url:
                fetched = self._fetch(url)
                if fetched:
                    out.append(fetched)
        return out, "image/png"

    def _fetch(self, url: str) -> bytes | None:
        try:
            with httpx.Client(timeout=_TIMEOUT_S) as client:
                r = client.get(url)
            return r.content if r.status_code == 200 else None
        except httpx.HTTPError:
            return None


def build(settings) -> Backend:
    """Factory used by load_backend to construct the litellm backend."""
    return Backend(settings)
