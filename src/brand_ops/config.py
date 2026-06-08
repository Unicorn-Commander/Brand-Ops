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
    )
