"""OpenRouter image backend + engine fallback tests.

The backend tests drive a mock httpx transport, so nothing leaves the process.
The engine tests confirm generate_asset routes to OpenRouter when an asset's
default backend has no key, and that an explicit backend is never swapped.
"""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from brand_ops import engine
from brand_ops.backends import openrouter
from brand_ops.backends.base import (
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
)
from brand_ops.config import Settings


def _settings(**kw) -> Settings:
    base = {"llm_api_key": "k", "llm_base_url": "https://gw/api/v1"}
    base.update(kw)
    return Settings(**base)


def _png_bytes(size: tuple[int, int] = (4, 4), color=(180, 80, 255, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _data_url(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def _image_response(raw: bytes, mime: str = "image/png") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "ok",
                    "images": [
                        {"type": "image_url", "image_url": {"url": _data_url(raw, mime)}}
                    ],
                }
            }
        ]
    }


def _mock_backend(monkeypatch, handler, settings: Settings | None = None):
    """Build the backend with its HTTP client swapped for a mock transport."""
    settings = settings or _settings()
    backend = openrouter.build(settings)

    def _client() -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": "Bearer k"},
            timeout=5.0,
        )

    monkeypatch.setattr(backend, "_client", _client)
    return backend


def test_health_reflects_key() -> None:
    assert openrouter.build(Settings()).health() is False
    assert openrouter.build(_settings()).health() is True
    # A dedicated OpenRouter key alone (no llm_*) is enough.
    assert openrouter.build(Settings(openrouter_api_key="or-key")).health() is True


def test_dedicated_openrouter_creds_take_precedence() -> None:
    """When openrouter_* is set it wins over llm_* (which points at the metered
    gateway in the integrated deploy), so image gen reaches OpenRouter directly."""
    s = _settings(
        llm_api_key="gateway-key",
        llm_base_url="http://unicorn-litellm:4000/v1",
        openrouter_api_key="or-key",
        openrouter_base_url="https://openrouter.ai/api/v1",
    )
    b = openrouter.build(s)
    assert b._key() == "or-key"
    assert b._base_url() == "https://openrouter.ai/api/v1"


def test_key_falls_back_to_llm_when_no_dedicated() -> None:
    """With no dedicated OpenRouter key, the key falls back to llm_api_key
    (single-key/vacuum deploy). The base url defaults to OpenRouter's endpoint so
    the openrouter-named backend always reaches OpenRouter, never the gateway."""
    b = openrouter.build(Settings(llm_api_key="k", openrouter_api_key=""))
    assert b._key() == "k"
    assert b._base_url() == "https://openrouter.ai/api/v1"


def test_reference_images_become_content_parts(monkeypatch, tmp_path) -> None:
    """With reference images the message content is an array (text + image_url),
    which is what carries the existing brand art to nano-banana for style match."""
    ref = tmp_path / "unicorn.png"
    ref.write_bytes(_png_bytes())
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(_png_bytes()))

    backend = _mock_backend(monkeypatch, handler)
    backend.generate(
        GenRequest(prompt="match this", width=1024, height=1024, reference_images=[ref])
    )
    content = seen["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "match this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_generate_returns_image_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _png_bytes()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(png))

    backend = _mock_backend(monkeypatch, handler)
    result = backend.generate(GenRequest(prompt="a logo", width=1024, height=1024))

    assert result.images == [png]
    assert result.mime == "image/png"
    assert result.backend == "openrouter"
    assert result.model == "google/gemini-3.1-flash-image-preview"
    assert seen["modalities"] == ["image", "text"]
    assert seen["image_config"]["aspect_ratio"] == "1:1"
    assert seen["messages"][0]["content"] == "a logo"


def test_generate_count_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _png_bytes()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_image_response(png))

    backend = _mock_backend(monkeypatch, handler)
    result = backend.generate(GenRequest(prompt="x", width=512, height=512, count=3))

    assert calls["n"] == 3
    assert len(result.images) == 3


def test_reference_images_become_content_array(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(_png_bytes())
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(_png_bytes()))

    backend = _mock_backend(monkeypatch, handler)
    backend.generate(
        GenRequest(prompt="edit", width=1024, height=1024, reference_images=[ref])
    )

    content = seen["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "edit"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    backend = _mock_backend(monkeypatch, handler)
    with pytest.raises(BackendError):
        backend.generate(GenRequest(prompt="x", width=512, height=512))


def test_no_images_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]})

    backend = _mock_backend(monkeypatch, handler)
    with pytest.raises(BackendError):
        backend.generate(GenRequest(prompt="x", width=512, height=512))


def test_missing_key_raises_not_configured() -> None:
    backend = openrouter.build(Settings())  # no llm_api_key
    with pytest.raises(BackendNotConfigured):
        backend.generate(GenRequest(prompt="x", width=512, height=512))


def test_aspect_ratio_snaps() -> None:
    backend = openrouter.build(_settings())
    assert backend._aspect_ratio(GenRequest(prompt="", width=1600, height=900)) == "16:9"
    assert backend._aspect_ratio(GenRequest(prompt="", width=1024, height=1024)) == "1:1"
    assert backend._aspect_ratio(GenRequest(prompt="", width=1080, height=1920)) == "9:16"


def test_image_size_buckets_and_hint() -> None:
    backend = openrouter.build(_settings())
    assert backend._image_size(GenRequest(prompt="", width=1024, height=1024)) == "1K"
    assert backend._image_size(GenRequest(prompt="", width=2048, height=2048)) == "2K"
    assert backend._image_size(GenRequest(prompt="", width=3200, height=1800)) == "4K"
    assert (
        backend._image_size(GenRequest(prompt="", width=10, height=10, resolution_hint="2K"))
        == "2K"
    )
    with pytest.raises(BackendError):
        backend._image_size(GenRequest(prompt="", width=10, height=10, resolution_hint="9K"))


class _FakeBackend:
    """A backend stub whose health depends on its name."""

    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self._healthy = healthy

    def health(self) -> bool:
        return self._healthy

    def generate(self, req: GenRequest) -> GenResult:
        return GenResult(images=[b"PNGDATA"], mime="image/png", backend=self.name, model="m")


def test_engine_falls_back_to_openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    # og_image defaults to the gemini backend; with no gemini key but an
    # OpenRouter key set, the engine should fall back to openrouter.
    monkeypatch.setattr(
        engine,
        "load_backend",
        lambda name, settings: _FakeBackend(name, healthy=(name == "openrouter")),
    )
    monkeypatch.setattr(engine, "save_result", lambda *a, **k: [])

    outcome = engine.generate_asset("magic-unicorn", "og_image", settings=_settings())

    assert outcome.result is not None
    assert outcome.result.backend == "openrouter"
    assert any("openrouter" in note for note in outcome.notes)


def test_engine_respects_explicit_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit, unconfigured backend is never silently swapped.
    monkeypatch.setattr(
        engine, "load_backend", lambda name, settings: _FakeBackend(name, healthy=False)
    )
    monkeypatch.setattr(engine, "save_result", lambda *a, **k: [])

    with pytest.raises(BackendNotConfigured):
        engine.generate_asset(
            "magic-unicorn", "og_image", backend="gemini", settings=_settings()
        )
