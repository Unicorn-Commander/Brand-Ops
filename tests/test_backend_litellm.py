"""LiteLLM gateway image backend tests + engine preference.

Drives a mock httpx transport (no network). Confirms the backend speaks the
OpenAI /v1/images/generations shape and that the engine prefers the gateway
when it is configured.
"""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

from brand_ops import engine
from brand_ops.backends import litellm as litellm_backend
from brand_ops.backends.base import (
    BackendError,
    BackendNotConfigured,
    GenRequest,
    GenResult,
)
from brand_ops.config import Settings


def _settings(**kw) -> Settings:
    base = {
        "litellm_base_url": "http://gw:4000",
        "litellm_api_key": "sk-virtual",
        "litellm_image_model": "gpt-image-1",
    }
    base.update(kw)
    return Settings(**base)


def _png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (180, 80, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _image_response(raw: bytes) -> dict:
    b64 = base64.b64encode(raw).decode("ascii")
    return {"data": [{"b64_json": b64}], "usage": {"total_tokens": 10}}


def _mock_backend(monkeypatch, handler, settings: Settings | None = None):
    settings = settings or _settings()
    backend = litellm_backend.build(settings)

    def _client() -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url=settings.litellm_base_url.rstrip("/"),
            headers={"Authorization": "Bearer sk-virtual"},
            timeout=5.0,
        )

    monkeypatch.setattr(backend, "_client", _client)
    return backend


def test_health_requires_url_key_and_model() -> None:
    assert litellm_backend.build(Settings()).health() is False
    assert litellm_backend.build(_settings()).health() is True
    assert litellm_backend.build(_settings(litellm_api_key="")).health() is False


def test_generate_decodes_b64(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _png_bytes()
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/images/generations"
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(png))

    backend = _mock_backend(monkeypatch, handler)
    result = backend.generate(GenRequest(prompt="a mark", width=1024, height=1024, count=2))

    assert result.images == [png]
    assert result.backend == "litellm"
    assert result.model == "gpt-image-1"
    assert seen["n"] == 2
    assert seen["size"] == "1024x1024"
    assert seen["prompt"] == "a mark"


def test_size_orientation() -> None:
    b = litellm_backend.build(_settings())
    assert b._size(GenRequest(prompt="", width=1024, height=1024)) == "1024x1024"
    assert b._size(GenRequest(prompt="", width=1536, height=768)) == "1536x1024"
    assert b._size(GenRequest(prompt="", width=768, height=1366)) == "1024x1536"


def test_non_200_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    backend = _mock_backend(monkeypatch, handler)
    with pytest.raises(BackendError):
        backend.generate(GenRequest(prompt="x", width=512, height=512))


def test_no_data_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    backend = _mock_backend(monkeypatch, handler)
    with pytest.raises(BackendError):
        backend.generate(GenRequest(prompt="x", width=512, height=512))


def test_not_configured_raises() -> None:
    with pytest.raises(BackendNotConfigured):
        litellm_backend.build(Settings()).generate(GenRequest(prompt="x", width=512, height=512))


def test_quality_defaults_to_medium_for_gpt_image(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(_png_bytes()))

    backend = _mock_backend(monkeypatch, handler)
    result = backend.generate(GenRequest(prompt="x", width=1024, height=1024))
    assert seen["quality"] == "medium"
    assert result.cost_cents is not None
    assert result.meta["quality"] == "medium"


def test_quality_override(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(_png_bytes()))

    backend = _mock_backend(monkeypatch, handler)
    backend.generate(GenRequest(prompt="x", width=512, height=512, extra={"quality": "low"}))
    assert seen["quality"] == "low"


def test_quality_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _mock_backend(
        monkeypatch, lambda r: httpx.Response(200, json=_image_response(_png_bytes()))
    )
    with pytest.raises(BackendError):
        backend.generate(GenRequest(prompt="x", width=512, height=512, extra={"quality": "ultra"}))


def test_quality_omitted_for_non_gpt_image(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_image_response(_png_bytes()))

    backend = _mock_backend(
        monkeypatch, handler, settings=_settings(litellm_image_model="dall-e-3")
    )
    backend.generate(GenRequest(prompt="x", width=512, height=512))
    assert "quality" not in seen


class _FakeBackend:
    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self._healthy = healthy

    def health(self) -> bool:
        return self._healthy

    def generate(self, req: GenRequest) -> GenResult:
        return GenResult(images=[b"PNG"], mime="image/png", backend=self.name, model="m")


def test_engine_prefers_gateway_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    # og_image defaults to gemini (no key); the fallback chain is [litellm, openrouter];
    # with litellm healthy it must win over openrouter.
    healthy = {"litellm", "openrouter"}
    monkeypatch.setattr(
        engine,
        "load_backend",
        lambda name, settings: _FakeBackend(name, healthy=(name in healthy)),
    )
    monkeypatch.setattr(engine, "save_result", lambda *a, **k: [])

    outcome = engine.generate_asset("magic-unicorn", "og_image", settings=_settings())

    assert outcome.result is not None
    assert outcome.result.backend == "litellm"
    assert any("litellm" in note for note in outcome.notes)
