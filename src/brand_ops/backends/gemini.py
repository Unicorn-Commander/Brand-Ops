"""Gemini (Nano Banana Pro) image backend.

A direct adapter onto Google's google-genai client. It satisfies the
ImageBackend protocol so the engine, CLI, MCP server, REST API, and autonomy
agents all drive it the same way as any other backend.

The optional google-genai / pillow deps are imported lazily inside the methods
that need them, so `import brand_ops.backends.gemini` works without the
`backends` extra installed (health() can still report False).

Unlike the moltbot nano-banana script this is built from, we never flatten RGBA
onto a white background: raw image bytes are returned as-is so any alpha
channel survives downstream (logos, sticker sheets, overlays).
"""
from __future__ import annotations

from pathlib import Path

from .base import (
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
)

# Default model when settings.gemini_model is unset and no req.extra override.
_DEFAULT_MODEL = "gemini-3-pro-image-preview"

# Google supports up to 14 reference images for multi-image editing/composition.
_MAX_REFERENCE_IMAGES = 14

# Valid image_size hints for the Gemini image config.
_VALID_RESOLUTIONS = ("1K", "2K", "4K")

# Per-image cost estimate in cents. Pro 4K is the priciest tier; flash is the
# cheap-draft path. These are deliberately rough estimates surfaced to callers.
_COST_FLASH_CENTS = 3.9
_COST_PRO_LOW_CENTS = 13.0   # pro at 1K / 2K
_COST_PRO_4K_CENTS = 24.0


class Backend:
    """Gemini image backend adapter."""

    name = "gemini"

    def __init__(self, settings) -> None:
        self._settings = settings

    def health(self) -> bool:
        """True when an API key is present; no network call."""
        return bool(self._settings.gemini_api_key)

    def generate(self, req: GenRequest) -> GenResult:
        api_key = self._settings.gemini_api_key
        if not api_key:
            raise BackendNotConfigured(
                "gemini backend has no API key (set GEMINI_API_KEY)"
            )

        # Lazy imports: keep the optional deps off the module top level so the
        # module imports cleanly without the `backends` extra installed.
        from google import genai
        from google.genai import types

        model = req.extra.get("model") or self._settings.gemini_model or _DEFAULT_MODEL

        ref_images = self._load_reference_images(req.reference_images)
        resolution = self._resolution(req)

        client = genai.Client(api_key=api_key)

        contents = [*ref_images, req.prompt] if ref_images else req.prompt
        config = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            image_config=types.ImageConfig(image_size=resolution),
        )

        # The model returns one image per call, so loop to reach req.count.
        count = max(1, req.count)
        images: list[bytes] = []
        notes: list[str] = []
        for _ in range(count):
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            images.extend(self._extract_images(response))

        if not images:
            raise BackendError("gemini returned no image data in the response")

        # We never paint a white background. If the caller asked for
        # transparency but the bytes carry no alpha, we just say so in meta.
        if req.transparent:
            if not self._any_has_alpha(images):
                notes.append("requested transparent but result has no alpha; returned as-is")

        meta: dict = {"resolution": resolution}
        if notes:
            meta["notes"] = notes

        return GenResult(
            images=images,
            mime="image/png",
            backend=self.name,
            model=model,
            cost_cents=self._cost_cents(model, resolution) * len(images),
            meta=meta,
        )

    # --- internals -----------------------------------------------------------

    def _load_reference_images(self, paths: list[Path]) -> list:
        """Open up to 14 reference images as PIL Images (lazy PIL import)."""
        if not paths:
            return []
        if len(paths) > _MAX_REFERENCE_IMAGES:
            raise BackendError(
                f"too many reference images ({len(paths)}); gemini supports at "
                f"most {_MAX_REFERENCE_IMAGES}"
            )

        from PIL import Image

        loaded = []
        for path in paths:
            try:
                loaded.append(Image.open(path))
            except Exception as exc:  # noqa: BLE001 - re-raise as a clear backend error
                raise BackendError(f"could not load reference image {path}: {exc}") from exc
        return loaded

    def _resolution(self, req: GenRequest) -> str:
        """Honor an explicit hint, else derive a tier from the larger dimension."""
        if req.resolution_hint:
            hint = req.resolution_hint.strip().upper()
            if hint not in _VALID_RESOLUTIONS:
                valid = ", ".join(_VALID_RESOLUTIONS)
                raise BackendError(
                    f"invalid resolution_hint {req.resolution_hint!r}; expected one of {valid}"
                )
            return hint

        longest = max(req.width, req.height)
        if longest >= 3000:
            return "4K"
        if longest >= 1500:
            return "2K"
        return "1K"

    def _extract_images(self, response) -> list[bytes]:
        """Pull raw image bytes from inline_data parts, preserving any alpha."""
        import base64

        images: list[bytes] = []
        parts = getattr(response, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = inline.data
            # The client usually hands back bytes; some transports send base64.
            if isinstance(data, str):
                data = base64.b64decode(data)
            if data:
                images.append(bytes(data))
        return images

    def _any_has_alpha(self, images: list[bytes]) -> bool:
        """Best-effort check whether any returned image carries an alpha channel."""
        from io import BytesIO

        from PIL import Image

        for raw in images:
            try:
                with Image.open(BytesIO(raw)) as img:
                    if img.mode in ("RGBA", "LA", "PA") or (
                        img.mode == "P" and "transparency" in img.info
                    ):
                        return True
            except Exception:  # noqa: BLE001 - alpha probe is advisory only
                continue
        return False

    def _cost_cents(self, model: str, resolution: str) -> float:
        """Rough per-image cost estimate in cents."""
        if "flash" in model.lower():
            return _COST_FLASH_CENTS
        if resolution == "4K":
            return _COST_PRO_4K_CENTS
        return _COST_PRO_LOW_CENTS


def build(settings) -> Backend:
    """Factory used by load_backend to construct the gemini backend."""
    return Backend(settings)
