"""OpenAI (gpt-image-1) image backend.

A direct adapter onto OpenAI's Python client. It satisfies the ImageBackend
protocol so the engine, CLI, MCP server, REST API, and autonomy agents all drive
it the same way as any other backend.

The optional `openai` dep is imported lazily inside the methods that need it, so
`import brand_ops.backends.openai` works without the `backends` extra installed
(health() can still report False).

Naming note: this module is brand_ops.backends.openai, which shadows the
third-party `openai` package *only* for code that does an implicit relative
import. Python 3 has no implicit relative imports, so `from openai import
OpenAI` inside a method always resolves to the installed third-party package,
not this module. `from __future__ import annotations` plus the lazy import keeps
the module-top namespace clean either way.

gpt-image-1 has native transparency: when req.transparent is set we ask for a
PNG with a transparent background, and we never paint a white background. Raw
image bytes are returned as-is so any alpha channel survives downstream (logos,
sticker sheets, overlays).
"""
from __future__ import annotations

from pathlib import Path

from .base import (
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
)

_MODEL = "gpt-image-1"

# gpt-image-1 only accepts a fixed set of sizes. We map the requested aspect
# ratio to the nearest supported one.
_SIZE_SQUARE = "1024x1024"
_SIZE_LANDSCAPE = "1536x1024"
_SIZE_PORTRAIT = "1024x1536"

# gpt-image-1 quality tiers. Default to medium; a caller may override via
# req.extra["quality"].
_VALID_QUALITIES = ("low", "medium", "high", "auto")
_DEFAULT_QUALITY = "medium"

# images.edit accepts up to 16 reference images.
_MAX_REFERENCE_IMAGES = 16

# Rough per-image cost estimate in cents, keyed by quality tier (square output;
# the wider/taller sizes cost a touch more but these stay deliberately rough).
# Surfaced to callers, not billed against.
_COST_BY_QUALITY = {
    "low": 1.1,
    "medium": 4.0,
    "high": 17.0,
    "auto": 4.0,
}


class Backend:
    """OpenAI gpt-image-1 image backend adapter."""

    name = "openai"

    def __init__(self, settings) -> None:
        self._settings = settings

    def health(self) -> bool:
        """True when an API key is present; no network call."""
        return bool(self._settings.openai_api_key)

    def generate(self, req: GenRequest) -> GenResult:
        api_key = self._settings.openai_api_key
        if not api_key:
            raise BackendNotConfigured(
                "openai backend has no API key (set OPENAI_API_KEY)"
            )

        # Lazy import: keep the optional dep off the module top level so the
        # module imports cleanly without the `backends` extra installed.
        from openai import OpenAI

        size = self._size(req)
        quality = self._quality(req)
        count = max(1, req.count)

        # Common kwargs for both generate and edit.
        kwargs: dict = {
            "model": _MODEL,
            "prompt": req.prompt,
            "size": size,
            "quality": quality,
            "n": count,
        }
        # Native transparency: PNG with a transparent background. We never
        # flatten onto white, so any alpha survives downstream.
        if req.transparent:
            kwargs["background"] = "transparent"
            kwargs["output_format"] = "png"

        client = OpenAI(api_key=api_key)

        opened = self._load_reference_images(req.reference_images)
        try:
            if opened:
                # Editing against the reference image(s) keeps outputs on brand.
                # The SDK accepts a single file or a list of files.
                image_arg = opened if len(opened) > 1 else opened[0]
                response = self._call(client.images.edit, image=image_arg, **kwargs)
            else:
                response = self._call(client.images.generate, **kwargs)
        finally:
            for fh in opened:
                try:
                    fh.close()
                except Exception:  # noqa: BLE001 - cleanup is best effort
                    pass

        images = self._extract_images(response)
        if not images:
            raise BackendError("openai returned no image data in the response")

        return GenResult(
            images=images,
            mime="image/png",
            backend=self.name,
            model=_MODEL,
            cost_cents=self._cost_cents(quality) * len(images),
            meta={"size": size, "transparent": req.transparent, "quality": quality},
        )

    # --- internals -----------------------------------------------------------

    def _size(self, req: GenRequest) -> str:
        """Map the requested width x height to the nearest supported size."""
        width = req.width or 0
        height = req.height or 0
        if width <= 0 or height <= 0 or width == height:
            return _SIZE_SQUARE
        return _SIZE_LANDSCAPE if width > height else _SIZE_PORTRAIT

    def _quality(self, req: GenRequest) -> str:
        """Honor an explicit req.extra['quality'], else default to medium."""
        raw = req.extra.get("quality")
        if raw is None:
            return _DEFAULT_QUALITY
        quality = str(raw).strip().lower()
        if quality not in _VALID_QUALITIES:
            valid = ", ".join(_VALID_QUALITIES)
            raise BackendError(
                f"invalid quality {raw!r}; expected one of {valid}"
            )
        return quality

    def _load_reference_images(self, paths: list[Path]) -> list:
        """Open up to 16 reference images as binary file handles for images.edit."""
        if not paths:
            return []
        if len(paths) > _MAX_REFERENCE_IMAGES:
            raise BackendError(
                f"too many reference images ({len(paths)}); openai supports at "
                f"most {_MAX_REFERENCE_IMAGES}"
            )

        opened: list = []
        for path in paths:
            try:
                opened.append(open(path, "rb"))
            except Exception as exc:  # noqa: BLE001 - re-raise as a clear backend error
                # Close anything already opened before bailing out.
                for fh in opened:
                    try:
                        fh.close()
                    except Exception:  # noqa: BLE001
                        pass
                raise BackendError(
                    f"could not load reference image {path}: {exc}"
                ) from exc
        return opened

    def _call(self, fn, **kwargs):
        """Invoke an OpenAI images call, wrapping failures as BackendError."""
        try:
            return fn(**kwargs)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize SDK/transport errors
            raise BackendError(f"openai image request failed: {exc}") from exc

    def _extract_images(self, response) -> list[bytes]:
        """Base64-decode the b64_json payload of each returned image."""
        import base64

        images: list[bytes] = []
        data = getattr(response, "data", None) or []
        for item in data:
            b64 = getattr(item, "b64_json", None)
            if not b64:
                continue
            images.append(base64.b64decode(b64))
        return images

    def _cost_cents(self, quality: str) -> float:
        """Rough per-image cost estimate in cents, keyed by quality tier."""
        return _COST_BY_QUALITY.get(quality, _COST_BY_QUALITY[_DEFAULT_QUALITY])


def build(settings) -> Backend:
    """Factory used by load_backend to construct the openai backend."""
    return Backend(settings)
