"""Centralised configuration for SB-Optima (Pydantic Settings).

Every environment variable the backend reads is declared once in the
:class:`Settings` class.  Modules import :func:`get_settings` instead of
calling ``os.environ`` directly.

Usage::

    from backend.config import get_settings

    settings = get_settings()
    if settings.enable_apply:
        ...
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings — populated from environment variables.

    All fields carry safe defaults so the backend can start even when
    *no* env vars are set (development / test mode).
    """

    # -- General ----------------------------------------------------------
    sboptima_env: str = Field(
        default="dev",
        description="Deployment environment: dev, staging, or prod.",
    )

    # -- Database ---------------------------------------------------------
    database_url: Optional[str] = Field(
        default=None,
        description="SQLAlchemy database URL (Postgres, SQLite, …).",
    )

    # -- Redis ------------------------------------------------------------
    redis_url: Optional[str] = Field(
        default=None,
        description="Redis connection URL.",
    )

    # -- Apply safety gate ------------------------------------------------
    enable_apply: bool = Field(
        default=False,
        alias="SB_OPTIMA_ENABLE_APPLY",
        description="Set to true to enable real price writes.",
    )

    # -- OpenAI / LLM ----------------------------------------------------
    openai_api_key: Optional[str] = Field(
        default=None,
        description="OpenAI API key for LLM-assisted features.",
    )
    openai_base_url: Optional[str] = Field(
        default=None,
        description="Custom OpenAI-compatible base URL.",
    )

    # -- CORS -------------------------------------------------------------
    #: Stored as a raw comma-separated string because pydantic-settings
    #: attempts JSON-parse on complex types (``list``) before validators
    #: run.  Use :meth:`get_cors_origins_list` to obtain a ``list[str]``.
    cors_origins: str = Field(
        default="",
        description="Comma-separated allowed origins for CORS.",
    )

    # -- Backend URL (consumed by Streamlit, declared here for docs) ------
    sb_optima_backend_url: str = Field(
        default="http://localhost:8000",
        description="URL the Streamlit frontend uses to reach the backend.",
    )

    # -- Postgres (consumed by Docker Compose, declared for completeness) -
    postgres_user: Optional[str] = Field(default=None)
    postgres_password: Optional[str] = Field(default=None)
    postgres_db: Optional[str] = Field(default=None)

    # -- Jobs / Arq -------------------------------------------------------
    job_result_ttl_s: int = Field(
        default=3600,
        description="TTL in seconds for job result keys in Redis.",
    )

    # -- Product cache (Task 3.2) -----------------------------------------
    product_cache_ttl_s: int = Field(
        default=900,
        description="TTL in seconds for cached product data (default 15 min).",
    )
    cache_key_salt: str = Field(
        default="change-me",
        description="Server-side salt mixed into cache key hashes.",
    )
    cache_max_payload_kb: int = Field(
        default=5120,
        description="Maximum payload size in KB allowed for a single cache entry.",
    )

    # -- SOAP rate limiting (Task 3.3) ------------------------------------
    soap_max_concurrent: int = Field(
        default=3,
        description="Max concurrent SOAP calls per caller key.",
    )
    soap_call_delay_s: float = Field(
        default=0.2,
        description="Minimum delay in seconds between successive SOAP calls per caller.",
    )
    soap_rate_limit_per_s: float = Field(
        default=5.0,
        description=(
            "Max SOAP calls per second per caller (token-bucket rate). "
            "Reserved for future use; currently only concurrency + delay "
            "are enforced."
        ),
    )

    # -- Crypto / Auth ----------------------------------------------------
    encryption_key: Optional[str] = Field(default=None)
    credential_cipher: str = Field(
        default="fernet",
        description="Encryption cipher used for credential vault (only 'fernet' is supported).",
    )
    allow_request_credentials_when_authed: bool = Field(
        default=False,
        description=(
            "When True and auth is required, allow request-supplied credentials "
            "in addition to vault credentials.  When False (default), only vault "
            "credentials are accepted when auth is enabled."
        ),
    )
    jwt_secret: Optional[str] = Field(
        default=None,
        description=(
            "Secret key for signing JWTs.  Required in prod; "
            "falls back to 'dev-secret' only when SBOPTIMA_ENV=dev."
        ),
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="Algorithm used when signing/verifying JWTs.",
    )
    jwt_access_token_exp_minutes: int = Field(
        default=60,
        description="Lifetime of an access token in minutes.",
    )
    sboptima_auth_required: bool = Field(
        default=False,
        description=(
            "When True, protected endpoints reject unauthenticated requests. "
            "When False (default), auth is optional — useful for local dev / migration."
        ),
    )

    # -- Observability (Task 9.1) -----------------------------------------
    metrics_enabled: bool = Field(
        default=False,
        description=(
            "When True, expose a /metrics endpoint (Prometheus format). "
            "The endpoint requires admin+ role when auth is enabled."
        ),
    )

    # -- Stripe / Billing (Task 8.1) --------------------------------------
    stripe_secret_key: Optional[str] = Field(
        default=None,
        description="Stripe secret key (server-side only, never expose to UI).",
    )
    stripe_webhook_secret: Optional[str] = Field(
        default=None,
        description="Stripe webhook signing secret (whsec_…).",
    )
    stripe_price_id_pro: Optional[str] = Field(
        default=None,
        description="Stripe Price ID for the Pro plan.",
    )
    stripe_price_id_enterprise: Optional[str] = Field(
        default=None,
        description="Stripe Price ID for the Enterprise plan.",
    )
    billing_enabled: bool = Field(
        default=True,
        description=(
            "Master switch for billing features.  When False or when Stripe "
            "keys are missing, billing endpoints return 503."
        ),
    )

    # -----------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------

    @field_validator("enable_apply", mode="before")
    @classmethod
    def _coerce_empty_to_false(cls, v):  # noqa: N805
        """Treat an empty string as *False* (env var present but blank)."""
        if isinstance(v, str) and v.strip() == "":
            return False
        return v

    # -----------------------------------------------------------------
    # Pydantic Settings config
    # -----------------------------------------------------------------

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Env file values do NOT override real env vars.
        "extra": "ignore",
        "populate_by_name": True,
    }

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def get_cors_origins_list(self) -> list[str]:
        """Parse :attr:`cors_origins` into a list of origin strings."""
        if not self.cors_origins:
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def get_jwt_secret(self) -> str:
        """Return the effective JWT signing secret.

        In *dev* mode (``sboptima_env == "dev"``), a missing secret falls
        back to ``"dev-secret"`` so that local development works without
        explicit configuration.  In any other environment a missing secret
        raises :class:`ValueError`.
        """
        if self.jwt_secret:
            return self.jwt_secret
        if self.sboptima_env == "dev":
            return "dev-secret"
        raise ValueError(
            "JWT_SECRET must be set when SBOPTIMA_ENV is not 'dev'."
        )

    # -----------------------------------------------------------------
    # Safe representation (no secrets)
    # -----------------------------------------------------------------

    #: Fields that must never appear in logs or public output.
    _SECRET_FIELDS: frozenset[str] = frozenset(
        {
            "database_url",
            "redis_url",
            "openai_api_key",
            "postgres_password",
            "encryption_key",
            "jwt_secret",
            "cache_key_salt",
            "stripe_secret_key",
            "stripe_webhook_secret",
        }
    )

    def to_safe_dict(self) -> dict:
        """Return a dict with secret fields redacted (``"***"``)."""
        data = self.model_dump()
        for key in self._SECRET_FIELDS:
            if key in data and data[key] is not None:
                data[key] = "***"
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The first call reads environment variables and ``.env``; subsequent
    calls return the same object.  Call ``get_settings.cache_clear()``
    in tests when env vars change between test cases.
    """
    return Settings()
