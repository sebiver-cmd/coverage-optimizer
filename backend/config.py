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

    # -- Crypto / Auth (placeholders for future tasks) --------------------
    encryption_key: Optional[str] = Field(default=None)
    jwt_secret: Optional[str] = Field(default=None)

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
