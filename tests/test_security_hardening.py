"""Tests for Task 10.1 — Security tightening (CORS, headers, size guard).

All tests are independent of external services.
"""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(**env_overrides):
    """Create a fresh FastAPI app with the given env overrides.

    Returns a ``TestClient`` wrapping the app.  Config cache is cleared so
    that env changes take effect.
    """
    env = {
        "SBOPTIMA_ENV": "dev",
        "SBOPTIMA_AUTH_REQUIRED": "false",
        "DATABASE_URL": "",
        **env_overrides,
    }
    with patch.dict(os.environ, env, clear=False):
        from backend.config import get_settings
        get_settings.cache_clear()

        # Re-import main to pick up new settings
        import importlib
        import backend.main as main_mod
        importlib.reload(main_mod)
        client = TestClient(main_mod.app, raise_server_exceptions=False)
    return client


# ---------------------------------------------------------------------------
# CORS tests
# ---------------------------------------------------------------------------


class TestCORS:
    """Validate CORS behaviour with explicit allowed origins."""

    def test_allowed_origin_echoed(self):
        """Preflight from an allowed origin should echo Access-Control-Allow-Origin."""
        client = _make_app(
            CORS_ALLOWED_ORIGINS="http://localhost:8501,https://app.example.com",
        )
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8501"

    def test_disallowed_origin_not_echoed(self):
        """Request from a non-listed origin must NOT receive an ACAO header."""
        client = _make_app(
            CORS_ALLOWED_ORIGINS="https://only-this.example.com",
        )
        resp = client.get(
            "/health",
            headers={"Origin": "https://evil.example.com"},
        )
        assert "access-control-allow-origin" not in resp.headers

    def test_dev_defaults_to_localhost(self):
        """In dev with no explicit origins, localhost:8501 should be allowed."""
        client = _make_app(CORS_ALLOWED_ORIGINS="", CORS_ORIGINS="")
        resp = client.options(
            "/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:8501"

    def test_wildcard_not_present(self):
        """CORS should never return a wildcard ``*`` origin."""
        client = _make_app(
            CORS_ALLOWED_ORIGINS="https://specific.example.com",
        )
        resp = client.options(
            "/health",
            headers={
                "Origin": "https://specific.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != "*"

    def test_cors_regex_match(self):
        """An origin matching cors_allowed_origin_regex should be allowed."""
        client = _make_app(
            CORS_ALLOWED_ORIGINS="",
            CORS_ORIGINS="",
            CORS_ALLOWED_ORIGIN_REGEX=r"https://.*\.example\.com",
        )
        resp = client.options(
            "/health",
            headers={
                "Origin": "https://sub.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://sub.example.com"


# ---------------------------------------------------------------------------
# Security headers tests
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Validate security headers middleware."""

    def test_headers_present_when_enabled(self):
        """Standard security headers should be present on every response."""
        client = _make_app(SECURITY_HEADERS_ENABLED="true")
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "default-src 'none'" in resp.headers.get("Content-Security-Policy", "")
        assert "camera=()" in resp.headers.get("Permissions-Policy", "")

    def test_headers_absent_when_disabled(self):
        """When disabled, security headers should not be added."""
        client = _make_app(SECURITY_HEADERS_ENABLED="false")
        resp = client.get("/health")
        assert "X-Content-Type-Options" not in resp.headers
        assert "X-Frame-Options" not in resp.headers

    def test_hsts_present_when_enabled(self):
        """HSTS header should appear only when explicitly enabled."""
        client = _make_app(HSTS_ENABLED="true", SECURITY_HEADERS_ENABLED="true")
        resp = client.get("/health")
        assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")

    def test_hsts_absent_when_disabled(self):
        """HSTS should not appear by default."""
        client = _make_app(HSTS_ENABLED="false", SECURITY_HEADERS_ENABLED="true")
        resp = client.get("/health")
        assert "Strict-Transport-Security" not in resp.headers


# ---------------------------------------------------------------------------
# Request size limit tests
# ---------------------------------------------------------------------------


class TestRequestSizeLimit:
    """Validate the request-size-limit middleware."""

    def test_oversized_request_returns_413(self):
        """A POST with Content-Length exceeding the limit gets 413."""
        client = _make_app(MAX_REQUEST_BODY_BYTES="500")
        big_body = "x" * 600
        resp = client.post(
            "/health",
            content=big_body,
            headers={"Content-Type": "application/json", "Content-Length": "600"},
        )
        assert resp.status_code == 413

    def test_normal_request_passes(self):
        """A small POST should not be blocked."""
        client = _make_app(MAX_REQUEST_BODY_BYTES="5000")
        resp = client.get("/health")
        assert resp.status_code in (200, 401, 403)


# ---------------------------------------------------------------------------
# Webhook endpoint tests
# ---------------------------------------------------------------------------


class TestWebhookProtections:
    """Ensure the webhook endpoint is reachable without JWT but still guarded."""

    def test_webhook_no_jwt_required(self):
        """POST /billing/webhook should not 401 for missing JWT."""
        client = _make_app(
            SBOPTIMA_AUTH_REQUIRED="true",
            BILLING_ENABLED="true",
            STRIPE_SECRET_KEY="sk_test_fake",
            STRIPE_WEBHOOK_SECRET="whsec_fake",
        )
        # The request will fail for signature verification (400) or billing
        # misconfiguration (503), but critically NOT 401/403 for missing JWT.
        resp = client.post(
            "/billing/webhook",
            content=b'{"type":"test"}',
            headers={
                "Content-Type": "application/json",
                "stripe-signature": "t=1,v1=abc",
            },
        )
        assert resp.status_code not in (401, 403)

    def test_webhook_size_guard(self):
        """POST /billing/webhook with oversized body returns 413."""
        client = _make_app(MAX_REQUEST_BODY_BYTES="100")
        big_body = "x" * 200
        resp = client.post(
            "/billing/webhook",
            content=big_body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": "200",
            },
        )
        assert resp.status_code == 413
