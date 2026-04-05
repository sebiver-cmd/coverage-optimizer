"""Tests for backend.config — centralised Pydantic Settings (Task 2.3).

Validates:
- Safe defaults when no env vars are set.
- Boolean parsing for ``SB_OPTIMA_ENABLE_APPLY``.
- Comma-separated list parsing for ``CORS_ORIGINS``.
- ``to_safe_dict()`` redacts secret fields.
- Settings integration with apply gating and /health.
"""

from __future__ import annotations

import pytest

from backend.config import Settings, get_settings


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    """Settings must have sensible defaults when no env vars are set."""

    def test_default_env(self, monkeypatch):
        monkeypatch.delenv("SBOPTIMA_ENV", raising=False)
        s = Settings()
        assert s.sboptima_env == "dev"

    def test_default_database_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        s = Settings()
        assert s.database_url is None

    def test_default_redis_url(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        s = Settings()
        assert s.redis_url is None

    def test_default_enable_apply(self, monkeypatch):
        monkeypatch.delenv("SB_OPTIMA_ENABLE_APPLY", raising=False)
        s = Settings()
        assert s.enable_apply is False

    def test_default_openai_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        s = Settings()
        assert s.openai_api_key is None

    def test_default_cors_origins(self, monkeypatch):
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        s = Settings()
        assert s.cors_origins == ""
        # In dev mode (default), empty origins fall back to localhost:8501
        assert s.get_cors_origins_list() == ["http://localhost:8501"]


# ---------------------------------------------------------------------------
# Boolean parsing — SB_OPTIMA_ENABLE_APPLY
# ---------------------------------------------------------------------------


class TestEnableApplyParsing:
    """``enable_apply`` must parse truthful strings correctly."""

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes"])
    def test_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", value)
        s = Settings()
        assert s.enable_apply is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", ""])
    def test_falsy_values(self, monkeypatch, value):
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", value)
        s = Settings()
        assert s.enable_apply is False


# ---------------------------------------------------------------------------
# List parsing — CORS_ORIGINS
# ---------------------------------------------------------------------------


class TestCorsOriginsParsing:
    """``get_cors_origins_list()`` must parse comma-separated strings."""

    def test_single_origin(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
        s = Settings()
        assert s.get_cors_origins_list() == ["http://localhost:3000"]

    def test_multiple_origins(self, monkeypatch):
        monkeypatch.setenv(
            "CORS_ORIGINS",
            "http://localhost:3000, http://localhost:8501",
        )
        s = Settings()
        assert s.get_cors_origins_list() == [
            "http://localhost:3000",
            "http://localhost:8501",
        ]

    def test_empty_string(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "")
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        s = Settings()
        # In dev mode (default), empty origins fall back to localhost:8501
        assert s.get_cors_origins_list() == ["http://localhost:8501"]

    def test_empty_string_prod(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "")
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setenv("SBOPTIMA_ENV", "prod")
        s = Settings()
        assert s.get_cors_origins_list() == []

    def test_trailing_comma(self, monkeypatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://a.com,http://b.com,")
        s = Settings()
        assert s.get_cors_origins_list() == ["http://a.com", "http://b.com"]


# ---------------------------------------------------------------------------
# Safe dict — secrets redacted
# ---------------------------------------------------------------------------


class TestSafeDict:
    """``to_safe_dict()`` must never include secret field values."""

    def test_secrets_redacted(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret123")
        monkeypatch.setenv("REDIS_URL", "redis://host:6379")
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        s = Settings()
        safe = s.to_safe_dict()

        assert safe["database_url"] == "***"
        assert safe["openai_api_key"] == "***"
        assert safe["redis_url"] == "***"
        # encryption_key is None → should remain None (not redacted)
        assert safe["encryption_key"] is None

    def test_non_secret_fields_visible(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_ENV", "staging")
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", "true")
        s = Settings()
        safe = s.to_safe_dict()

        assert safe["sboptima_env"] == "staging"
        assert safe["enable_apply"] is True

    def test_no_raw_secret_strings(self, monkeypatch):
        """Full scan: no secret value must appear anywhere in to_safe_dict."""
        secrets = {
            "DATABASE_URL": "postgresql://user:pass@host/db",
            "OPENAI_API_KEY": "sk-secret-key-12345",
            "REDIS_URL": "redis://host:6379",
            "POSTGRES_PASSWORD": "supersecret",
            "ENCRYPTION_KEY": "fernet-key-abc",
            "JWT_SECRET": "jwt-secret-xyz",
        }
        for k, v in secrets.items():
            monkeypatch.setenv(k, v)

        s = Settings()
        safe = s.to_safe_dict()
        safe_str = str(safe)

        for secret_value in secrets.values():
            assert secret_value not in safe_str, (
                f"Secret value leaked in to_safe_dict: {secret_value}"
            )


# ---------------------------------------------------------------------------
# get_settings() caching
# ---------------------------------------------------------------------------


class TestGetSettings:
    """``get_settings()`` returns a cached instance."""

    def test_returns_settings(self):
        s = get_settings()
        assert isinstance(s, Settings)

    def test_cached(self):
        a = get_settings()
        b = get_settings()
        assert a is b

    def test_cache_clear_refreshes(self, monkeypatch):
        s1 = get_settings()
        get_settings.cache_clear()
        monkeypatch.setenv("SBOPTIMA_ENV", "prod")
        s2 = get_settings()
        assert s2.sboptima_env == "prod"
        assert s1 is not s2
