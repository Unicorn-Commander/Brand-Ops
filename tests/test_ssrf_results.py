"""SSRF gate on provider-returned image URLs (T3.3).

The image backends fetch the generated-image URL out of the provider's JSON. That
URL is attacker-influenced if an upstream is compromised, so both backends now
route the fetch through the shared ``is_url_fetchable`` guard before connecting
and re-validate every redirect hop. These tests prove:

  1. A result URL resolving to a private / loopback / cloud-metadata address is
     refused (the fetch returns ``None``, no bytes), for both backends.
  2. A result URL on a public address is fetched normally (the happy path is
     unchanged), for both backends.
  3. A public URL that 30x-redirects to an internal target is refused after the
     redirect (the up-front check cannot see the hop; ``_redirect_chain_is_safe``
     catches it).
  4. The guard is the same one ``web_fetch`` uses (imported, not duplicated), and
     OpenRouter routes a non-data result URL through that guarded fetch.

No test here touches the network: IP-literal URLs are classified without DNS, and
httpx is driven through ``httpx.MockTransport``.
"""
from __future__ import annotations

import httpx
import pytest

from brand_ops.agents import web_fetch
from brand_ops.backends import litellm as litellm_backend
from brand_ops.backends import openrouter as openrouter_backend
from brand_ops.config import Settings

# Public IP literal: classified safe with no DNS lookup, so the fetch proceeds.
_PUBLIC_URL = "http://93.184.216.34/generated.png"
# Cloud metadata endpoint (link-local): must be refused before any connection.
_METADATA_URL = "http://169.254.169.254/generated.png"
# RFC-1918 private address: also refused.
_PRIVATE_URL = "http://10.0.0.5/generated.png"
_PNG = b"\x89PNG\r\n\x1a\n-fake-but-opaque-bytes"


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, module, handler) -> None:
    """Make the backend module's ``httpx.Client`` use a MockTransport.

    ``_fetch`` constructs ``httpx.Client(timeout=..., follow_redirects=True)``
    inline, so swap the module's ``httpx`` for a shim whose ``Client`` injects the
    mock transport (and honors ``follow_redirects`` so redirect chains are built).
    """

    class _ShimHttpx:
        HTTPError = httpx.HTTPError

        @staticmethod
        def Client(*args, **kwargs):  # noqa: N802 - mirrors httpx.Client
            follow = kwargs.get("follow_redirects", False)
            return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow)

    monkeypatch.setattr(module, "httpx", _ShimHttpx)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_PNG)


# ─────────────────────── the guard is reused, not copied ────────────────────


def test_backends_import_the_shared_guard() -> None:
    # Both backends must reference the exact ``web_fetch`` guard, so the SSRF
    # classification can never drift from the intake fetcher's.
    assert litellm_backend.is_url_fetchable is web_fetch.is_url_fetchable
    assert openrouter_backend.is_url_fetchable is web_fetch.is_url_fetchable


# ─────────────────────── litellm result-URL fetch ───────────────────────────


def test_litellm_fetch_rejects_metadata_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = litellm_backend.build(Settings())
    # If the guard let this through the handler would return bytes; it must not.
    _patch_httpx_client(monkeypatch, litellm_backend, _ok_handler)
    assert backend._fetch(_METADATA_URL) is None


def test_litellm_fetch_rejects_private_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = litellm_backend.build(Settings())
    _patch_httpx_client(monkeypatch, litellm_backend, _ok_handler)
    assert backend._fetch(_PRIVATE_URL) is None


def test_litellm_fetch_accepts_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = litellm_backend.build(Settings())
    _patch_httpx_client(monkeypatch, litellm_backend, _ok_handler)
    assert backend._fetch(_PUBLIC_URL) == _PNG


def test_litellm_fetch_rejects_redirect_to_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    # A public URL that 302s to the metadata endpoint: the up-front check passes,
    # but the post-fetch hop re-validation must reject the internal landing URL.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": _METADATA_URL})
        return httpx.Response(200, content=_PNG)

    backend = litellm_backend.build(Settings())
    _patch_httpx_client(monkeypatch, litellm_backend, handler)
    assert backend._fetch(_PUBLIC_URL) is None


def test_litellm_extract_images_fetches_url_items(monkeypatch: pytest.MonkeyPatch) -> None:
    # The provider returned a url item (not b64): it is fetched through the guard.
    backend = litellm_backend.build(Settings())
    _patch_httpx_client(monkeypatch, litellm_backend, _ok_handler)
    images, mime = backend._extract_images({"data": [{"url": _PUBLIC_URL}]})
    assert images == [_PNG]
    assert mime == "image/png"


def test_litellm_extract_images_drops_internal_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = litellm_backend.build(Settings())
    _patch_httpx_client(monkeypatch, litellm_backend, _ok_handler)
    images, _ = backend._extract_images({"data": [{"url": _METADATA_URL}]})
    assert images == []


# ─────────────────────── openrouter result-URL fetch ────────────────────────


def _openrouter_backend() -> openrouter_backend.Backend:
    return openrouter_backend.build(Settings(openrouter_api_key="sk-or"))


def test_openrouter_fetch_rejects_metadata_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _openrouter_backend()
    _patch_httpx_client(monkeypatch, openrouter_backend, _ok_handler)
    assert backend._fetch(_METADATA_URL) is None


def test_openrouter_fetch_accepts_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _openrouter_backend()
    _patch_httpx_client(monkeypatch, openrouter_backend, _ok_handler)
    assert backend._fetch(_PUBLIC_URL) == _PNG


def test_openrouter_fetch_rejects_redirect_to_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "93.184.216.34":
            return httpx.Response(302, headers={"Location": _PRIVATE_URL})
        return httpx.Response(200, content=_PNG)

    backend = _openrouter_backend()
    _patch_httpx_client(monkeypatch, openrouter_backend, handler)
    assert backend._fetch(_PUBLIC_URL) is None


def test_openrouter_extract_images_fetches_remote_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-data image_url in the OpenRouter response is fetched via the guard and
    # its bytes returned; an internal one yields nothing.
    backend = _openrouter_backend()
    _patch_httpx_client(monkeypatch, openrouter_backend, _ok_handler)

    public_payload = {
        "choices": [{"message": {"images": [{"image_url": {"url": _PUBLIC_URL}}]}}]
    }
    images, _ = backend._extract_images(public_payload)
    assert images == [_PNG]

    internal_payload = {
        "choices": [{"message": {"images": [{"image_url": {"url": _METADATA_URL}}]}}]
    }
    images, _ = backend._extract_images(internal_payload)
    assert images == []


def test_openrouter_extract_images_still_decodes_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # The existing data-URL happy path must be untouched by the new fetch branch.
    import base64

    backend = _openrouter_backend()
    b64 = base64.b64encode(_PNG).decode("ascii")
    payload = {
        "choices": [
            {"message": {"images": [{"image_url": {"url": f"data:image/png;base64,{b64}"}}]}}
        ]
    }
    images, mime = backend._extract_images(payload)
    assert images == [_PNG]
    assert mime == "image/png"
