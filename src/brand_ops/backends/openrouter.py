"""OpenRouter image backend.

A direct adapter onto OpenRouter's image-generation API: the OpenAI-compatible
/chat/completions endpoint driven with modalities ["image", "text"]. It reuses
the same OpenRouter credentials the text agents already use (settings.llm_api_key
and settings.llm_base_url), so one key serves both the Director's reasoning and
image generation across OpenRouter's image catalog (nano-banana 2, GPT image,
Flux, and the rest).

This is the BYO-key, standalone ("in a vacuum") path: it always reaches
OpenRouter directly. The integrated suite deploy routes the same chat/completions
image protocol through the metered gateway instead, via the ``litellm`` backend,
which holds the provider creds centrally. The two backends share that protocol in
``_chat_image``; this module supplies only OpenRouter's credentials, endpoint, and
its own SSRF-gated result fetch.

httpx is a hard dependency; nothing here needs the optional `backends` extra, so
this backend is always available once an OpenRouter key (or gateway) is set.
"""
from __future__ import annotations

import httpx

from ..agents.web_fetch import is_url_fetchable
from . import _chat_image
from .base import BackendError, BackendNotConfigured, GenRequest, GenResult

# Generous timeout: image models are slower than chat completions.
_TIMEOUT_S = 180.0


def _redirect_chain_is_safe(response: httpx.Response) -> bool:
    """True when the landing URL and every redirect hop pass the SSRF guard.

    The up-front check on the initial URL cannot see a 30x to an internal target,
    so re-validate the final URL plus each hop in ``response.history``. Any one
    unsafe hop fails the whole fetch.
    """
    if not is_url_fetchable(str(response.url)):
        return False
    return all(is_url_fetchable(str(hop.url)) for hop in response.history)


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

        model = (
            req.extra.get("model")
            or self._settings.openrouter_image_model
            or _chat_image.DEFAULT_MODEL
        )
        # Dual-output models (Gemini, GPT image) want ["image", "text"]; an
        # image-only model can be driven with ["image"] via req.extra.
        modalities = req.extra.get("modalities")
        body = _chat_image.build_body(model, req, modalities=modalities)

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
            meta={"endpoint": self._base_url(), "modalities": modalities or ["image", "text"]},
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

    # Thin wrappers over the shared protocol so the aspect/size snapping has one
    # definition; kept as methods because the backend tests call them directly.
    def _aspect_ratio(self, req: GenRequest) -> str:
        return _chat_image.aspect_ratio(req)

    def _image_size(self, req: GenRequest) -> str:
        return _chat_image.image_size(req)

    def _extract_images(self, data: dict) -> tuple[list[bytes], str | None]:
        """Pull image bytes from choices[0].message.images[], fetching any remote
        URL through this backend's SSRF-gated ``_fetch``."""
        return _chat_image.extract_chat_images(data, self._fetch)

    def _fetch(self, url: str) -> bytes | None:
        """Fetch a provider-returned remote image URL, gated against SSRF.

        Validate the URL through the shared guard before connecting, follow
        redirects, then re-validate every hop so a 30x to an internal target is
        rejected. A public provider CDN passes unchanged. Defined here (not in the
        shared helper) so the SSRF tests can patch this module's ``httpx``.
        """
        if not is_url_fetchable(url):
            return None
        try:
            with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as client:
                r = client.get(url)
            if not _redirect_chain_is_safe(r):
                return None
            return r.content if r.status_code == 200 else None
        except httpx.HTTPError:
            return None


def build(settings) -> Backend:
    """Factory used by load_backend to construct the openrouter backend."""
    return Backend(settings)
