"""Tests for Task 9.1 — Observability hardening.

Covers:
- Request-ID middleware (echo / generate)
- redact_dict utility
- Metrics endpoint (503 when disabled, 200 when enabled)
- Authorization header never appears in structured logs
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_app():
    """Import the app fresh (settings cache already cleared by conftest)."""
    from backend.main import app
    return app


def _client(**env_overrides) -> TestClient:
    """Return a ``TestClient`` with optional env-var overrides applied."""
    return TestClient(_get_app())


# ---------------------------------------------------------------------------
# 1) Request-ID middleware
# ---------------------------------------------------------------------------


class TestRequestID:
    """X-Request-ID propagation."""

    def test_provided_header_is_echoed(self):
        client = _client()
        rid = str(uuid.uuid4())
        resp = client.get("/health", headers={"X-Request-ID": rid})
        assert resp.headers.get("x-request-id") == rid

    def test_generated_when_absent(self):
        client = _client()
        resp = client.get("/health")
        rid = resp.headers.get("x-request-id")
        assert rid is not None
        # Must be a valid UUID-4
        uuid.UUID(rid, version=4)

    def test_request_id_present_on_error_responses(self):
        """Even 404s should carry the header."""
        client = _client()
        resp = client.get("/no-such-route")
        assert resp.headers.get("x-request-id") is not None


# ---------------------------------------------------------------------------
# 2) redact_dict
# ---------------------------------------------------------------------------


class TestRedactDict:
    """Sensitive key scrubbing."""

    def test_removes_sensitive_keys(self):
        from backend.logging_config import redact_dict

        raw = {
            "authorization": "Bearer secret-jwt-token",
            "api_password": "p@ssw0rd",
            "password": "hunter2",
            "jwt_secret": "super-secret",
            "encryption_key": "my-key",
            "stripe_secret_key": "sk_live_xxx",
            "stripe_webhook_secret": "whsec_xxx",
            "safe_field": "visible",
        }
        redacted = redact_dict(raw)

        # Sensitive keys replaced
        for key in (
            "authorization",
            "api_password",
            "password",
            "jwt_secret",
            "encryption_key",
            "stripe_secret_key",
            "stripe_webhook_secret",
        ):
            assert redacted[key] == "***REDACTED***", f"{key} not redacted"

        # Safe field preserved
        assert redacted["safe_field"] == "visible"

    def test_case_insensitive(self):
        from backend.logging_config import redact_dict

        raw = {"Authorization": "Bearer xyz", "PASSWORD": "secret"}
        redacted = redact_dict(raw)
        assert redacted["Authorization"] == "***REDACTED***"
        assert redacted["PASSWORD"] == "***REDACTED***"

    def test_nested_dict(self):
        from backend.logging_config import redact_dict

        raw = {"headers": {"authorization": "Bearer tok", "host": "example.com"}}
        redacted = redact_dict(raw)
        assert redacted["headers"]["authorization"] == "***REDACTED***"
        assert redacted["headers"]["host"] == "example.com"

    def test_original_not_mutated(self):
        from backend.logging_config import redact_dict

        raw = {"password": "secret", "ok": True}
        _ = redact_dict(raw)
        assert raw["password"] == "secret"  # unchanged

    def test_extra_keys(self):
        from backend.logging_config import redact_dict

        raw = {"custom_secret": "val", "ok": True}
        redacted = redact_dict(raw, extra_keys=frozenset({"custom_secret"}))
        assert redacted["custom_secret"] == "***REDACTED***"
        assert redacted["ok"] is True


# ---------------------------------------------------------------------------
# 3) Metrics endpoint
# ---------------------------------------------------------------------------


class TestMetricsEndpoint:
    """GET /metrics behaviour."""

    def test_503_when_disabled(self, monkeypatch):
        monkeypatch.setenv("METRICS_ENABLED", "false")
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "false")
        client = _client()
        resp = client.get("/metrics")
        assert resp.status_code == 503

    def test_200_when_enabled(self, monkeypatch):
        monkeypatch.setenv("METRICS_ENABLED", "true")
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "false")
        client = _client()
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "http_requests_total" in body

    def test_contains_domain_metrics(self, monkeypatch):
        monkeypatch.setenv("METRICS_ENABLED", "true")
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "false")
        client = _client()
        resp = client.get("/metrics")
        body = resp.text
        # All declared metric families should appear in output
        for name in (
            "http_requests_total",
            "http_request_duration_seconds",
            "soap_calls_total",
            "quota_exceeded_total",
            "billing_webhook_events_total",
        ):
            assert name in body, f"{name} missing from /metrics output"


# ---------------------------------------------------------------------------
# 4) Structured logging — Authorization header must not appear
# ---------------------------------------------------------------------------


class _CapturingHandler(logging.Handler):
    """Collects formatted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord):
        self.records.append(self.format(record))


class TestStructuredLogging:
    """Ensure secrets never leak into log output."""

    def test_authorization_header_not_in_logs(self, monkeypatch):
        """Access log must not contain the raw Authorization value."""
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "false")

        from backend.logging_config import JSONFormatter

        handler = _CapturingHandler()
        handler.setFormatter(JSONFormatter())
        access_logger = logging.getLogger("sboptima.access")
        access_logger.addHandler(handler)
        access_logger.setLevel(logging.DEBUG)

        try:
            client = _client()
            secret_token = "super-secret-jwt-value-12345"
            client.get(
                "/health",
                headers={"Authorization": f"Bearer {secret_token}"},
            )

            combined = "\n".join(handler.records)
            assert secret_token not in combined, (
                "Authorization token leaked into access log!"
            )
        finally:
            access_logger.removeHandler(handler)

    def test_json_formatter_produces_valid_json(self):
        from backend.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        line = formatter.format(record)
        obj = json.loads(line)
        assert obj["msg"] == "hello world"
        assert obj["level"] == "INFO"
        assert "ts" in obj
        assert "logger" in obj

    def test_json_formatter_includes_extras(self):
        from backend.logging_config import JSONFormatter

        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="req",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc-123"
        record.tenant_id = "t-1"
        record.user_id = "u-1"
        record.path = "/health"
        record.method = "GET"
        record.status_code = 200
        record.duration_ms = 42.5

        line = formatter.format(record)
        obj = json.loads(line)
        assert obj["request_id"] == "abc-123"
        assert obj["tenant_id"] == "t-1"
        assert obj["user_id"] == "u-1"
        assert obj["path"] == "/health"
        assert obj["method"] == "GET"
        assert obj["status_code"] == 200
        assert obj["duration_ms"] == 42.5


# ---------------------------------------------------------------------------
# 5) Metrics helpers — domain counters
# ---------------------------------------------------------------------------


class TestMetricsHelpers:
    """record_* helpers increment counters."""

    def test_record_soap_call(self):
        from backend.metrics import SOAP_CALLS_TOTAL, record_soap_call

        before = SOAP_CALLS_TOTAL._value.get()
        record_soap_call()
        assert SOAP_CALLS_TOTAL._value.get() == before + 1

    def test_record_quota_exceeded(self):
        from backend.metrics import QUOTA_EXCEEDED_TOTAL, record_quota_exceeded

        label = "optimize_job"
        before = QUOTA_EXCEEDED_TOTAL.labels(action=label)._value.get()
        record_quota_exceeded(label)
        assert QUOTA_EXCEEDED_TOTAL.labels(action=label)._value.get() == before + 1

    def test_record_billing_webhook(self):
        from backend.metrics import BILLING_WEBHOOK_EVENTS_TOTAL, record_billing_webhook

        evt = "checkout.session.completed"
        before = BILLING_WEBHOOK_EVENTS_TOTAL.labels(type=evt)._value.get()
        record_billing_webhook(evt)
        assert BILLING_WEBHOOK_EVENTS_TOTAL.labels(type=evt)._value.get() == before + 1

    def test_record_http_request(self):
        from backend.metrics import HTTP_REQUESTS_TOTAL, record_http_request

        before = HTTP_REQUESTS_TOTAL.labels(method="GET", path="/test", status="200")._value.get()
        record_http_request("GET", "/test", 200, 0.05)
        assert HTTP_REQUESTS_TOTAL.labels(method="GET", path="/test", status="200")._value.get() == before + 1
