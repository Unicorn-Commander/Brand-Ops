"""OpenRouter image backend.

A direct adapter onto OpenRouter's image-generation API: the OpenAI-compatible
/chat/completions endpoint driven with modalities ["image", "text"]. It reuses
the same OpenRouter credentials the text agents already use (settings.llm_api_key
and settings.llm_base_url), so one key serves both the Director's reasoning and
image generation across OpenRouter's image catalog (nano-banana 2, GPT image,
Flux, and the rest).

This is the unified path. Point llm_base_url at the Ops-Center gateway instead
of OpenRouter and this same backend meters image spend centrally, with provider
keys held only in Ops-Center rather than copied into every app.

httpx is a hard dependency; nothing here needs the optional `backends` extra, so
this backend is always available once an OpenRouter key (or gateway) is set.
"""
from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .base import BackendError, BackendNotConfigured, GenRequest, GenResult

# Default to nano-banana 2 (Pro-level quality at Flash speed and cost). Env
# OPENROUTER_IMAGE_MODEL overrides it; req.extra["model"] overrides per call.
_DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview"

# OpenRouter image_size buckets. 1K is the documented default.
_VALID_SIZES = ("0.5K", "1K", "2K", "4K")

# Generous timeout: image models are slower than chat completions.
_TIMEOUT_S = 180.0

# Standard aspect ratios OpenRouter maps to pixel dimensions. We snap the
# requested width:height to the nearest of these.
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


class Backend:
    """OpenRouter image backend adapter."""

    name = "openrouter"

    def __init__(self, settings) -> None:
        self._settings = settings

    def _key(self) -> str:
        """The OpenRouter key. Prefer the dedicated one so reference-image
        generation reaches OpenRouter directly even when llm_* points at the
        metered gateway; fall back to llm_api_key for single-key deploys."""
        return self._settings.openrouter_api_key or self._settings.llm_api_key

    def _base_url(self) -> str:
        """The OpenRouter base url, with the same precedence as the key."""
        return self._settings.openrouter_base_url or self._settings.llm_base_url

    def health(self) -> bool:
        """True when an OpenRouter key (or gateway key) is present; no network call."""
        return bool(self._key())

    def generate(self, req: GenRequest) -> GenResult:
        api_key = self._key()
        if not api_key:
            raise BackendNotConfigured(
                "openrouter backend has no API key (set OPENROUTER_API_KEY or LLM_API_KEY)"
            )

        model = req.extra.get("model") or self._settings.openrouter_image_model or _DEFAULT_MODEL
        # Dual-output models (Gemini, GPT image) want ["image", "text"]; an
        # image-only model can be driven with ["image"] via req.extra.
        modalities = req.extra.get("modalities") or ["image", "text"]
        body = {
            "model": model,
            "messages": [{"role": "user", "content": self._build_content(req)}],
            "modalities": modalities,
            "image_config": {
                "aspect_ratio": self._aspect_ratio(req),
                "image_size": self._image_size(req),
            },
            "stream": False,
        }

        images: list[bytes] = []
        mime = "image/png"
        # The image models return one image per call, so loop to reach req.count.
        try:
            with self._client() as client:
                for _ in range(max(1, req.count)):
                    resp = client.post("/chat/completions", json=body)
                    if resp.status_code != 200:
                        raise BackendError(
                            f"openrouter image request failed ({resp.status_code}): "
                            f"{resp.text[:300]}"
                        )
                    batch, batch_mime = self._extract_images(resp.json())
                    images.extend(batch)
                    if batch_mime:
                        mime = batch_mime
        except httpx.HTTPError as exc:
            raise BackendError(f"openrouter image request error: {exc}") from exc

        if not images:
            raise BackendError("openrouter returned no image data in the response")

        return GenResult(
            images=images,
            mime=mime,
            backend=self.name,
            model=model,
            meta={"endpoint": self._base_url(), "modalities": modalities},
        )

    # --- internals -----------------------------------------------------------

    def _client(self) -> httpx.Client:
        """Build the HTTP client. Isolated so tests can supply a mock transport."""
        headers = {
            "Authorization": f"Bearer {self._key()}",
            "Content-Type": "application/json",
            # OpenRouter attribution headers; harmless against a gateway.
            "HTTP-Referer": "https://brand-ops.magicunicorn.dev",
            "X-Title": "Brand-Ops",
        }
        base = self._base_url().rstrip("/")
        return httpx.Client(base_url=base, headers=headers, timeout=_TIMEOUT_S)

    def _build_content(self, req: GenRequest):
        """Plain string for text-to-image; a content array when editing with refs."""
        if not req.reference_images:
            return req.prompt
        parts: list[dict] = [{"type": "text", "text": req.prompt}]
        for path in req.reference_images:
            parts.append({"type": "image_url", "image_url": {"url": self._data_url(path)}})
        return parts

    def _data_url(self, path: Path) -> str:
        try:
            raw = Path(path).read_bytes()
        except OSError as exc:
            raise BackendError(f"could not read reference image {path}: {exc}") from exc
        mime = _MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "image/png")
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _aspect_ratio(self, req: GenRequest) -> str:
        """Snap the requested dimensions to the nearest supported aspect ratio."""
        if req.width <= 0 or req.height <= 0:
            return "1:1"
        target = req.width / req.height
        return min(_ASPECT_RATIOS, key=lambda ar: abs(ar[1] - target))[0]

    def _image_size(self, req: GenRequest) -> str:
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

    def _extract_images(self, data: dict) -> tuple[list[bytes], str | None]:
        """Pull base64 image bytes from choices[0].message.images[]."""
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
            decoded, item_mime = self._decode_data_url(url)
            if decoded:
                out.append(decoded)
                mime = item_mime or mime
        return out, mime

    def _decode_data_url(self, url: str) -> tuple[bytes | None, str | None]:
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


def build(settings) -> Backend:
    """Factory used by load_backend to construct the openrouter backend."""
    return Backend(settings)
