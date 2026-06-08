"""Recraft image backend — the brand and vector specialist.

A direct adapter onto Recraft's REST API (https://external.api.recraft.ai/v1).
It satisfies the ImageBackend protocol so the engine, CLI, MCP server, REST API,
and autonomy agents all drive it the same way as any other backend.

Why Recraft sits in the suite: it is the brand/vector specialist. It is the only
backend that emits true SVG (scalable vector paths, not a rasterized trace),
honors an exact brand color palette via its ``controls.colors`` knob, supports
brand styles, and offers a dedicated background-removal pass for clean logo/icon
alpha.

httpx is imported lazily inside the methods that need it (and json/base64 too),
so ``import brand_ops.backends.recraft`` works without the ``backends`` extra
installed. health() can still report False with no network call.

API contract targeted (confirmed against the live Recraft docs, June 2026):
  - Base URL:        https://external.api.recraft.ai/v1
  - Generate:        POST /images/generations   (Authorization: Bearer <key>)
  - Remove bg:       POST /images/removeBackground  (multipart/form-data, file=)
  - Vector output:   style="vector_illustration" with a vector-capable model
                     returns a URL pointing at a real .svg file.
  - Raster output:   style="realistic_image" (default) returns a PNG URL/b64.
  - Brand palette:   controls={"colors": [{"rgb": [r, g, b]}, ...]}
  - Sizes:           pixel-pair strings, e.g. "1024x1024", "1365x1024".
  - response_format: "url" (default) or "b64_json".
See the report accompanying this build for the exact source pages.
"""
from __future__ import annotations

from .base import (
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
)

# Recraft REST base. Every call hangs off this.
_BASE_URL = "https://external.api.recraft.ai/v1"
_GENERATIONS_PATH = "/images/generations"
_REMOVE_BACKGROUND_PATH = "/images/removeBackground"

# Default generation model. recraftv3 is the SOTA brand/vector model and is the
# documented default; it serves both raster (realistic_image) and vector
# (vector_illustration) styles depending on the requested style string.
_DEFAULT_MODEL = "recraftv3"

# Recraft styles. The brand/vector specialist work lives in vector_illustration
# (true SVG) and realistic_image (raster). Callers may override via
# req.extra["style"] (e.g. "icon", "digital_illustration", or a substyle like
# "vector_illustration/line_art").
_STYLE_RASTER_DEFAULT = "realistic_image"
_STYLE_VECTOR_DEFAULT = "vector_illustration"

# Supported output size strings (pixel pairs) per the Recraft generations docs.
# We never send a size Recraft would reject; req.width x req.height is mapped to
# the closest supported aspect ratio below.
_SUPPORTED_SIZES = (
    "1024x1024",
    "1365x1024",
    "1024x1365",
    "1536x1024",
    "1024x1536",
    "1820x1024",
    "1024x1820",
    "1024x1707",
    "1707x1024",
    "1280x1024",
    "1024x1280",
    "1024x1434",
    "1434x1024",
    "1024x576",
    "576x1024",
)
_DEFAULT_SIZE = "1024x1024"

# Per-image cost estimates in cents (rough, surfaced to callers). Vector costs
# more than raster; background removal adds a small per-image pass.
_COST_RASTER_CENTS = 4.0
_COST_VECTOR_CENTS = 8.0
_COST_REMOVE_BG_CENTS = 4.0

# Network timeout. Image generation can take a while; keep it generous.
_TIMEOUT_SECONDS = 120.0


class Backend:
    """Recraft image backend adapter (brand styles, exact colors, SVG, bg-removal)."""

    name = "recraft"

    def __init__(self, settings) -> None:
        self._settings = settings

    def health(self) -> bool:
        """True when an API key is present; no network call."""
        return bool(self._settings.recraft_api_key)

    def generate(self, req: GenRequest) -> GenResult:
        api_key = self._settings.recraft_api_key
        if not api_key:
            raise BackendNotConfigured(
                "recraft backend has no API key (set RECRAFT_API_KEY)"
            )

        # Lazy import: keep httpx off the module top level so the module imports
        # cleanly without the `backends` extra installed.
        import httpx

        model = req.extra.get("model") or _DEFAULT_MODEL
        size = self._size(req)
        is_vector = bool(req.vector)
        style = self._style(req, is_vector)

        payload: dict = {
            "prompt": req.prompt,
            "model": model,
            "style": style,
            "size": size,
            "n": max(1, req.count),
            # Always ask for a URL; we fetch the bytes ourselves. (b64_json works
            # too but URL keeps the JSON small for batches.)
            "response_format": "url",
        }

        # Brand palette: Recraft expects controls.colors as a list of RGB
        # objects, each {"rgb": [r, g, b]} with 0-255 ints. We accept hex
        # ("#1a2b3c"), "rgb(r,g,b)", or [r,g,b]/(r,g,b) tuples in req.extra
        # and normalize to that shape. Optional controls.background_color uses
        # the same single-color shape.
        controls = self._build_controls(req)
        if controls:
            payload["controls"] = controls

        # For raster output a transparent background means: generate, then run
        # Recraft's dedicated background-removal pass for clean alpha. (Vector
        # SVGs are already transparent by nature, so we skip it there.)
        want_remove_bg = bool(req.transparent) and not is_vector

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(base_url=_BASE_URL, timeout=_TIMEOUT_SECONDS) as client:
            resp = self._post_json(client, _GENERATIONS_PATH, headers, payload)
            entries = self._result_entries(resp)
            if not entries:
                raise BackendError("recraft returned no image data in the response")

            images: list[bytes] = [self._fetch_entry_bytes(client, e) for e in entries]

            removed_bg = False
            if want_remove_bg:
                images = [self._remove_background(client, api_key, img) for img in images]
                removed_bg = True

        mime = "image/svg+xml" if is_vector else "image/png"
        per_image = _COST_VECTOR_CENTS if is_vector else _COST_RASTER_CENTS
        if removed_bg:
            per_image += _COST_REMOVE_BG_CENTS

        meta: dict = {
            "style": style,
            "size": size,
            "vector": is_vector,
        }
        if controls.get("colors"):
            meta["colors"] = controls["colors"]
        if removed_bg:
            meta["background_removed"] = True
        credits = self._credits(resp)
        if credits is not None:
            meta["credits_used"] = credits

        return GenResult(
            images=images,
            mime=mime,
            backend=self.name,
            model=model,
            cost_cents=per_image * len(images),
            meta=meta,
        )

    # --- internals -----------------------------------------------------------

    def _style(self, req: GenRequest, is_vector: bool) -> str:
        """Resolve the Recraft style string.

        An explicit req.extra["style"] always wins (lets callers pick "icon" or
        a substyle like "vector_illustration/line_art"). Otherwise we default to
        the vector or raster brand style based on req.vector.
        """
        override = req.extra.get("style")
        if override:
            return str(override)
        return _STYLE_VECTOR_DEFAULT if is_vector else _STYLE_RASTER_DEFAULT

    def _size(self, req: GenRequest) -> str:
        """Map req.width x req.height to the closest supported Recraft size.

        Recraft only accepts a fixed set of pixel-pair size strings, so we pick
        the supported option whose aspect ratio is nearest the request, breaking
        ties toward the larger pixel area. An explicit req.extra["size"] that is
        already supported is honored verbatim.
        """
        override = req.extra.get("size")
        if override and str(override) in _SUPPORTED_SIZES:
            return str(override)

        w = req.width if req.width and req.width > 0 else 1024
        h = req.height if req.height and req.height > 0 else 1024
        target_ratio = w / h

        best = _DEFAULT_SIZE
        best_key: tuple[float, int] | None = None
        for size in _SUPPORTED_SIZES:
            sw, sh = (int(x) for x in size.split("x"))
            ratio_delta = abs((sw / sh) - target_ratio)
            # Sort by aspect-ratio closeness first, then prefer larger area.
            key = (ratio_delta, -(sw * sh))
            if best_key is None or key < best_key:
                best_key = key
                best = size
        return best

    def _build_controls(self, req: GenRequest) -> dict:
        """Build the Recraft controls object (brand colors + background color).

        controls.colors: a list of preferred brand colors, each as
        {"rgb": [r, g, b]} with 0-255 integers. controls.background_color uses
        the same single-color shape. We accept hex/rgb()/tuple inputs from
        req.extra and normalize. req.extra["artistic_level"] (0-5) and
        req.extra["no_text"] (bool) pass through if present.
        """
        controls: dict = {}

        colors = req.extra.get("colors")
        if colors:
            normalized = [self._to_rgb_control(c) for c in colors]
            controls["colors"] = [c for c in normalized if c is not None]

        bg = req.extra.get("background_color")
        if bg is not None:
            rgb = self._to_rgb_control(bg)
            if rgb is not None:
                controls["background_color"] = rgb

        artistic_level = req.extra.get("artistic_level")
        if artistic_level is not None:
            controls["artistic_level"] = int(artistic_level)

        no_text = req.extra.get("no_text")
        if no_text is not None:
            controls["no_text"] = bool(no_text)

        return controls

    def _to_rgb_control(self, color) -> dict | None:
        """Normalize one color into Recraft's {"rgb": [r, g, b]} control shape.

        Accepts: "#rrggbb" / "rrggbb", "#rgb", "rgb(r, g, b)", and
        list/tuple [r, g, b]. Returns None for anything unparseable so a single
        bad swatch never sinks the whole request.
        """
        if isinstance(color, (list, tuple)):
            if len(color) >= 3:
                return {"rgb": [self._clamp_channel(color[i]) for i in range(3)]}
            return None

        if isinstance(color, dict):
            # Already a control object, or {"r":..,"g":..,"b":..}.
            if "rgb" in color and isinstance(color["rgb"], (list, tuple)):
                vals = list(color["rgb"])[:3]
                if len(vals) == 3:
                    return {"rgb": [self._clamp_channel(v) for v in vals]}
            if all(k in color for k in ("r", "g", "b")):
                return {"rgb": [self._clamp_channel(color[k]) for k in ("r", "g", "b")]}
            return None

        if isinstance(color, str):
            return self._parse_color_string(color)

        return None

    def _parse_color_string(self, raw: str) -> dict | None:
        """Parse a hex ("#1a2b3c"/"#abc") or "rgb(r, g, b)" string to a control."""
        s = raw.strip()
        low = s.lower()

        if low.startswith("rgb"):
            inner = s[s.find("(") + 1 : s.rfind(")")] if "(" in s and ")" in s else ""
            parts = [p.strip() for p in inner.split(",") if p.strip()]
            if len(parts) >= 3:
                try:
                    return {"rgb": [self._clamp_channel(int(float(p))) for p in parts[:3]]}
                except ValueError:
                    return None
            return None

        hexpart = s[1:] if s.startswith("#") else s
        if len(hexpart) == 3:  # shorthand #abc -> #aabbcc
            hexpart = "".join(ch * 2 for ch in hexpart)
        if len(hexpart) == 6:
            try:
                return {
                    "rgb": [
                        int(hexpart[0:2], 16),
                        int(hexpart[2:4], 16),
                        int(hexpart[4:6], 16),
                    ]
                }
            except ValueError:
                return None
        return None

    @staticmethod
    def _clamp_channel(value) -> int:
        """Coerce a channel to an int in [0, 255]."""
        try:
            n = int(round(float(value)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(255, n))

    def _post_json(self, client, path: str, headers: dict, payload: dict) -> dict:
        """POST JSON and return the parsed body, raising a clear BackendError."""
        import httpx

        try:
            r = client.post(path, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise BackendError(f"recraft request to {path} failed: {exc}") from exc
        if r.status_code >= 400:
            raise BackendError(
                f"recraft {path} returned HTTP {r.status_code}: {r.text[:500]}"
            )
        try:
            return r.json()
        except ValueError as exc:
            raise BackendError(f"recraft {path} returned non-JSON body") from exc

    def _result_entries(self, resp: dict) -> list[dict]:
        """Pull the per-image entries out of the response.

        Recraft returns {"data": [ {"url": ...} | {"b64_json": ...}, ... ]} (with
        a top-level "image" object for some single-image utility responses).
        """
        data = resp.get("data")
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        single = resp.get("image")
        if isinstance(single, dict):
            return [single]
        return []

    def _fetch_entry_bytes(self, client, entry: dict) -> bytes:
        """Return raw bytes for one result entry, from b64_json or its url."""
        import base64

        b64 = entry.get("b64_json")
        if b64:
            try:
                return base64.b64decode(b64)
            except (ValueError, TypeError) as exc:
                raise BackendError("recraft returned undecodable b64_json") from exc

        url = entry.get("url")
        if url:
            return self._download(client, url)

        raise BackendError("recraft result entry had neither url nor b64_json")

    def _download(self, client, url: str) -> bytes:
        """Download bytes from a Recraft result URL (no auth header needed)."""
        import httpx

        try:
            # Absolute URL; pass-through (base_url is ignored for absolute URLs).
            r = client.get(url)
        except httpx.HTTPError as exc:
            raise BackendError(f"recraft asset download failed: {exc}") from exc
        if r.status_code >= 400:
            raise BackendError(
                f"recraft asset download returned HTTP {r.status_code}"
            )
        return r.content

    def _remove_background(self, client, api_key: str, image: bytes) -> bytes:
        """Run Recraft's background-removal pass on raster bytes for clean alpha.

        POST /images/removeBackground takes the image as multipart/form-data
        (field "file") and returns a url/b64_json result, same as generations.
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        files = {"file": ("image.png", image, "image/png")}
        data = {"response_format": "url"}

        import httpx

        try:
            r = client.post(
                _REMOVE_BACKGROUND_PATH, headers=headers, files=files, data=data
            )
        except httpx.HTTPError as exc:
            raise BackendError(f"recraft background removal failed: {exc}") from exc
        if r.status_code >= 400:
            raise BackendError(
                f"recraft removeBackground returned HTTP {r.status_code}: {r.text[:500]}"
            )
        try:
            body = r.json()
        except ValueError as exc:
            raise BackendError("recraft removeBackground returned non-JSON body") from exc

        entries = self._result_entries(body)
        if not entries:
            raise BackendError("recraft removeBackground returned no image")
        return self._fetch_entry_bytes(client, entries[0])

    def _credits(self, resp: dict):
        """Best-effort read of credits consumed, if the API reported them."""
        for key in ("credits", "credits_used"):
            if key in resp:
                return resp[key]
        return None


def build(settings) -> Backend:
    """Factory used by load_backend to construct the recraft backend."""
    return Backend(settings)
