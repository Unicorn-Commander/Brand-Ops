"""Runtime settings.

Plain pydantic plus os.getenv, matching the ecosystem house style (the suite
does not use pydantic-settings). Secrets come from the environment only and are
never committed. Backend credentials are blank by default, which means that
backend reports unhealthy rather than crashing.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import BaseModel


class Settings(BaseModel):
    # Image backends
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-pro-image-preview"
    openai_api_key: str = ""
    recraft_api_key: str = ""
    comfyui_url: str = "http://localhost:8188"
    flux_model: str = "flux1-dev"
    # OpenRouter image generation. In a single-key "vacuum" deploy it can reuse
    # the agent LLM credentials (llm_api_key / llm_base_url). In the integrated
    # deploy the text path points at the metered gateway, which CANNOT carry
    # reference images, so the image backend needs its own direct OpenRouter
    # credentials to do reference-conditioned generation (matching existing brand
    # art). These take precedence over llm_* in the openrouter backend; when
    # blank it falls back to llm_*. Default is nano-banana 2; any OpenRouter image
    # slug works (openai/gpt-5.4-image-2, black-forest-labs/flux.2-pro, ...).
    openrouter_image_model: str = "google/gemini-3.1-flash-image-preview"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # LiteLLM gateway (the suite inference gateway). When set, Brand-Ops holds no
    # provider keys: it calls the gateway with a gateway-issued virtual key and
    # the gateway holds the real provider creds and meters + marks up the spend.
    # base_url is the gateway root (no /v1); the backend posts /v1/images/generations.
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    litellm_image_model: str = "gpt-image-1"
    # When an asset's default backend has no key and the caller did not request
    # one explicitly, fall back through this chain, using the first healthy one.
    # The gateway (litellm) is preferred when configured; OpenRouter is the
    # standalone fallback (same key the agents use, works the moment it is set).
    fallback_image_backends: list[str] = ["litellm", "openrouter"]

    # Brigade (autonomy + federation hub)
    brigade_base_url: str = "https://brigade.unicorncommander.ai"
    brigade_api_key: str = ""

    # Storage (Garage S3 output mirror), wired in WS5
    garage_s3_endpoint: str = ""
    garage_s3_access_key: str = ""
    garage_s3_secret_key: str = ""
    garage_s3_region: str = "garage"
    garage_s3_bucket: str = "brand-ops-assets"

    # Auth (Keycloak uchub realm), wired with the service surfaces
    keycloak_realm_url: str = "https://auth.unicorncommander.ai/realms/uchub"
    keycloak_client_id: str = "brand-ops"
    keycloak_client_secret: str = ""

    # Database, wired in WS1
    database_url: str = ""

    # Agent LLM. OpenAI-compatible gateway (the Ops-Center gateway in prod, or
    # OpenRouter for local eval). Qwen-first by default; the strong model is the
    # escalation for the research and critique agents. Exact ids are env tunable.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    agent_model_default: str = "qwen/qwen3.6-35b-a3b"
    agent_model_strong: str = "anthropic/claude-opus-4.1"

    # Orator (the suite TTS service) for agent voices. When set, Brand-Ops can
    # refresh the voice catalog live from orator and synthesize short voice
    # previews; when blank (the default), the catalog falls back to the curated
    # built-in roster and preview is reported unavailable. Brand-Ops never stores a
    # voice's audio: it binds a voice id to an agent and, on request, proxies one
    # short utterance to this single configured service (never a caller-supplied
    # host), so there is no SSRF surface. base_url is the orator root (it serves
    # /voices and /v1/audio/speech).
    orator_base_url: str = ""

    # Live-site capture for new-brand intake. The intake "Research" stage fetches
    # the operator-supplied URL to ground the draft (no Brigade required). The
    # suite has hit Cloudflare bot blocks with default/library User-Agents, so the
    # fetcher presents as a real desktop browser by default; override per deploy
    # when a target needs different UA tuning.
    intake_fetch_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 BrandOps/0.3 "
        "(+https://unicorncommander.ai; brand intake site capture)"
    )


@lru_cache
def get_settings() -> Settings:
    d = Settings()
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", d.gemini_model),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        recraft_api_key=os.getenv("RECRAFT_API_KEY", ""),
        comfyui_url=os.getenv("COMFYUI_URL", d.comfyui_url),
        flux_model=os.getenv("FLUX_MODEL", d.flux_model),
        openrouter_image_model=os.getenv("OPENROUTER_IMAGE_MODEL", d.openrouter_image_model),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", d.openrouter_base_url),
        litellm_base_url=os.getenv("LITELLM_BASE_URL", ""),
        litellm_api_key=os.getenv("LITELLM_API_KEY", ""),
        litellm_image_model=os.getenv("LITELLM_IMAGE_MODEL", d.litellm_image_model),
        fallback_image_backends=(
            [x.strip() for x in os.getenv("FALLBACK_IMAGE_BACKENDS", "").split(",") if x.strip()]
            or d.fallback_image_backends
        ),
        orator_base_url=os.getenv("ORATOR_BASE_URL", ""),
        brigade_base_url=os.getenv("BRIGADE_BASE_URL", d.brigade_base_url),
        brigade_api_key=os.getenv("BRIGADE_API_KEY", ""),
        garage_s3_endpoint=os.getenv("GARAGE_S3_ENDPOINT", ""),
        garage_s3_access_key=os.getenv("GARAGE_S3_ACCESS_KEY", ""),
        garage_s3_secret_key=os.getenv("GARAGE_S3_SECRET_KEY", ""),
        garage_s3_region=os.getenv("GARAGE_S3_REGION", d.garage_s3_region),
        garage_s3_bucket=os.getenv("GARAGE_S3_BUCKET", d.garage_s3_bucket),
        keycloak_realm_url=os.getenv("KEYCLOAK_REALM_URL", d.keycloak_realm_url),
        keycloak_client_id=os.getenv("KEYCLOAK_CLIENT_ID", d.keycloak_client_id),
        keycloak_client_secret=os.getenv("KEYCLOAK_CLIENT_SECRET", ""),
        database_url=os.getenv("DATABASE_URL", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", d.llm_base_url),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY", ""),
        agent_model_default=os.getenv("AGENT_MODEL_DEFAULT", d.agent_model_default),
        agent_model_strong=os.getenv("AGENT_MODEL_STRONG", d.agent_model_strong),
        intake_fetch_user_agent=os.getenv(
            "INTAKE_FETCH_USER_AGENT", d.intake_fetch_user_agent
        ),
    )
