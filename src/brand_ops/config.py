"""Runtime settings.

Plain pydantic plus os.getenv, matching the ecosystem house style (the suite
does not use pydantic-settings). Secrets come from the environment only and are
never committed. Backend credentials are blank by default, which means that
backend reports unhealthy rather than crashing.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import BaseModel

_log = logging.getLogger("brand_ops")


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` when unset or malformed."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("invalid float for %s=%r; using default %s", name, raw, default)
        return default


class Settings(BaseModel):
    # Image backends
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-pro-image-preview"
    openai_api_key: str = ""
    recraft_api_key: str = ""
    comfyui_url: str = "http://localhost:8188"
    flux_model: str = "flux1-dev"
    # OpenRouter image generation: the BYO-key, standalone ("vacuum") path. It can
    # reuse the agent LLM credentials (llm_api_key / llm_base_url) or take its own
    # dedicated openrouter_* creds, and it always reaches OpenRouter directly. In
    # the integrated deploy the reference-conditioned path runs through the metered
    # gateway instead (the litellm backend now carries reference images over
    # /chat/completions), so this backend is the fallback for deploys without the
    # gateway. Default is nano-banana 2; any OpenRouter image slug works
    # (openai/gpt-5.4-image-2, black-forest-labs/flux.2-pro, ...).
    openrouter_image_model: str = "google/gemini-3.1-flash-image-preview"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # LiteLLM gateway (the suite inference gateway). When set, Brand-Ops holds no
    # provider keys: it calls the gateway with a gateway-issued virtual key and
    # the gateway holds the real provider creds and meters + marks up the spend.
    # base_url is the gateway root (no /v1). The default model is the
    # reference-conditioned chat-image model (Gemini / nano-banana), which the
    # backend drives over /chat/completions so the existing brand art is carried
    # through; set a gpt-image-1 / dall-e-* model to use the prompt-only
    # /v1/images/generations endpoint instead.
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    litellm_image_model: str = "google/gemini-3.1-flash-image-preview"
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
    # Enforce delegated AGENT-token scopes (each agent's scopes_required in the
    # registry) on the agent-run path. Default OFF: today the suite's Brigade
    # issuer does not mint brandops:* scopes (it passes through the subject's KC
    # scopes and Brand-Ops is not yet registered as a federated app), so enforcing
    # would gate a delegation that does not exist yet. Flip on once the Brigade
    # app-registration + scope-minting contract is confirmed. Human SSO callers and
    # the unscoped CLI are always exempt; only delegated act/agent tokens are gated.
    enforce_agent_scopes: bool = False

    # Hosted-generation entitlement gate (paid-vs-free). Distinct from the dollar
    # spend cap (BRAND_OPS_IMAGE_CAP_CENTS): this is the on/off that separates a
    # Free tier (on-device + card paths only) from a paid tier (hosted, model-
    # backed generation). See entitlement.py, entitlements_client.py, and the
    # go-to-market plan.
    #   * enforce: the master switch. Default OFF, so the gate is a complete
    #     no-op and production behaves exactly as before until an operator flips
    #     it on per node once the entitlements endpoint is wired. When on, the gate
    #     fails CLOSED on an unknown entitlement (the opposite of the soft cost
    #     gate) because it protects shared-key spend.
    #   * allowlist: workspace ids (and/or org ids) that always pass even when
    #     enforced, so internal / dogfood / owner workspaces keep working the
    #     moment enforcement is first enabled. Empty by default.
    #   * endpoint_url: the suite Ops-Center entitlements endpoint
    #     (GET /api/v1/entitlements). Brand-Ops forwards the caller's verified
    #     Keycloak bearer to it and reads the returned org feature_keys. EMPTY by
    #     default = the resolver is dormant (no network call, the gate sees
    #     UNKNOWN). Resolution (URL set) and enforcement (enforce on) are
    #     INDEPENDENT: set the URL first to confirm the right grants resolve in the
    #     logs, then flip enforce. Per node: dogfood and prod each have their own.
    #   * feature_key: the canonical suite entitlement string that grants hosted
    #     generation for Brand-Ops (`brand_ops_access`). Present in the endpoint's
    #     list => ALLOW; a successful response without it => DENY.
    #     Setting the URL makes resolution run once per request-context on EVERY
    #     authed request (cached, so at most one endpoint call per caller per TTL),
    #     not only on generation -- so the timeout below bounds its reach.
    #   * cache_ttl_seconds: how long a resolved grant is cached per caller, so a
    #     burst of generations is one endpoint call, not one per asset. ~60s means
    #     an upgrade takes effect within ~60s (the endpoint's documented freshness).
    #   * http_timeout_seconds: the per-call timeout. Short and fail-safe: the
    #     resolver never raises into the request; on a timeout/5xx it uses the last
    #     good grant (fail-soft for a known caller) or UNKNOWN (fail-closed for a
    #     never-seen caller, only ever blocking when enforce is also on). Kept tight
    #     because, when the URL is set, it bounds added latency on all authed routes.
    entitlement_enforced: bool = False
    entitlement_allowlist: list[str] = []
    entitlement_endpoint_url: str = ""
    entitlement_feature_key: str = "brand_ops_access"
    entitlement_cache_ttl_seconds: float = 60.0
    entitlement_http_timeout_seconds: float = 3.0

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


class ConfigError(RuntimeError):
    """A required runtime setting is missing or invalid in a production profile."""


def validate_runtime_config() -> Settings:
    """Fail fast at boot when a prod deploy is missing a required setting.

    "Prod profile" is keyed on ``api.auth.auth_required()`` -- one reader, shared
    with the request path, whose default is now ON. That flag is on for every real
    deployment and is switched off EXPLICITLY (``AUTH_REQUIRED=false``) for the
    CLI, local dev, and the tests, so this gate only ever fires where it should.
    When the profile is prod it asserts the two settings the surface cannot safely
    run without and that otherwise fail late and obscurely:

      * ``DATABASE_URL`` -- without it the tenancy DB is unreachable, so every
        authed caller silently sees no brands (the read path fails closed). Better
        to refuse to boot than to serve an empty, broken surface.
      * the bootstrap workspace -- ``BRAND_OPS_BOOTSTRAP_WORKSPACE_ID`` is the
        uuid the deploy's file brands are stamped into; without it the seed binds
        brands to a generated/placeholder workspace no real membership points at,
        so again the brands are invisible. (The slug has a safe default, so only
        the id is required here.)

    Returns the validated :class:`Settings` for convenience. Raises
    :class:`ConfigError` with a single message naming every missing key, so a
    deploy sees one clear error rather than discovering them one boot at a time.
    Never called from import; the application entrypoint calls it once at startup.
    """
    # One reader for the flag, shared with the request path. Imported lazily
    # because ``api.auth`` imports this module at module scope. Keeping a second
    # copy of the parse here is how the two could disagree about what an unset
    # AUTH_REQUIRED means -- and it now means "on", so they must not.
    from .api.auth import auth_required as _auth_required

    auth_required = _auth_required()
    settings = get_settings()
    if not auth_required:
        # Dev / CLI / MCP / tests: no production guarantees are required, so this
        # never raises here. But a set DATABASE_URL means a real deployment is
        # talking to a tenancy DB while serving every request as the anonymous,
        # unscoped principal (which sees every tenant's brands). That is a deploy
        # that forgot to set AUTH_REQUIRED. Warn loudly (the compose default is
        # now fail-secure, so this only fires on an explicit override) without
        # raising, to keep the CLI and the DB-less tests working.
        if settings.database_url:
            _log.warning(
                "AUTH_REQUIRED is off but DATABASE_URL is set: this looks like a "
                "deployment serving UNAUTHENTICATED requests, where every caller "
                "is the anonymous principal and sees every tenant's brands. Set "
                "AUTH_REQUIRED=true in this deployment (only unset it for explicit "
                "local, CLI, or MCP use)."
            )
        return settings

    missing: list[str] = []
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if not os.getenv("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID", "").strip():
        missing.append("BRAND_OPS_BOOTSTRAP_WORKSPACE_ID")
    if missing:
        names = ", ".join(missing)
        raise ConfigError(
            "AUTH_REQUIRED is on (production profile) but required settings are "
            f"missing: {names}. Set them, or set AUTH_REQUIRED=false for "
            "local/CLI use. (AUTH_REQUIRED now DEFAULTS to on, so UNSETTING it "
            "is what produces this error, not what clears it.)"
        )
    return settings


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
        enforce_agent_scopes=os.getenv("BRAND_OPS_ENFORCE_AGENT_SCOPES", "").strip().lower()
        in ("1", "true", "yes", "on"),
        entitlement_enforced=os.getenv("BRAND_OPS_ENTITLEMENT_ENFORCED", "").strip().lower()
        in ("1", "true", "yes", "on"),
        entitlement_allowlist=[
            x.strip()
            for x in os.getenv("BRAND_OPS_ENTITLEMENT_ALLOWLIST", "").split(",")
            if x.strip()
        ],
        entitlement_endpoint_url=os.getenv("BRAND_OPS_ENTITLEMENT_URL", "").strip(),
        entitlement_feature_key=os.getenv("BRAND_OPS_ENTITLEMENT_FEATURE_KEY", "").strip()
        or d.entitlement_feature_key,
        entitlement_cache_ttl_seconds=_env_float(
            "BRAND_OPS_ENTITLEMENT_CACHE_TTL", d.entitlement_cache_ttl_seconds
        ),
        entitlement_http_timeout_seconds=_env_float(
            "BRAND_OPS_ENTITLEMENT_HTTP_TIMEOUT", d.entitlement_http_timeout_seconds
        ),
        database_url=os.getenv("DATABASE_URL", ""),
        llm_base_url=os.getenv("LLM_BASE_URL", d.llm_base_url),
        llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY", ""),
        agent_model_default=os.getenv("AGENT_MODEL_DEFAULT", d.agent_model_default),
        agent_model_strong=os.getenv("AGENT_MODEL_STRONG", d.agent_model_strong),
        intake_fetch_user_agent=os.getenv(
            "INTAKE_FETCH_USER_AGENT", d.intake_fetch_user_agent
        ),
    )
