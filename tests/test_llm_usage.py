"""Tests for Task 5.3 — LLM key management with tenant-aware usage tracking.

Covers:
1. ``record_llm_usage`` — structured log contains tenant_id, tokens_used, model.
2. ``get_monthly_llm_usage`` — usage accumulates correctly.
3. Tenant isolation — one tenant's usage doesn't affect another's view.
4. ``check_llm_limit`` — unlimited when limit=0.
5. ``check_llm_limit`` — raises ValueError when exceeded.
6. ``tracked_llm_call`` — rejected when limit exceeded.
7. ``_default_llm_call`` — backward compatible (accepts tenant_id as keyword-only).
8. Config field ``openai_monthly_token_limit`` exists with default 0.
9. Admin endpoint includes LLM usage in tenant detail.
"""

from __future__ import annotations

import logging
import os
import uuid
from unittest.mock import patch

import pytest

from backend.config import Settings, get_settings
from backend.llm_usage import (
    _usage_store,
    check_llm_limit,
    get_monthly_llm_usage,
    record_llm_usage,
    reset_usage_store,
    tracked_llm_call,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_usage_store():
    """Reset the in-memory usage store before each test."""
    reset_usage_store()
    yield
    reset_usage_store()


# ---------------------------------------------------------------------------
# 1) record_llm_usage logs correctly
# ---------------------------------------------------------------------------


def test_record_llm_usage_logs_correctly(caplog):
    """Structured log must contain tenant_id, tokens_used, and model."""
    tid = str(uuid.uuid4())
    with caplog.at_level(logging.INFO, logger="backend.llm_usage"):
        record_llm_usage(tenant_id=tid, tokens_used=100, model="gpt-4o-mini")

    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.tenant_id == tid
    assert rec.tokens_used == 100
    assert rec.model == "gpt-4o-mini"
    assert rec.month  # should be a YYYY-MM string


# ---------------------------------------------------------------------------
# 2) Monthly usage tracking
# ---------------------------------------------------------------------------


def test_monthly_usage_tracking():
    """Usage accumulates correctly across multiple calls."""
    tid = str(uuid.uuid4())
    record_llm_usage(tenant_id=tid, tokens_used=100, model="gpt-4o-mini")
    record_llm_usage(tenant_id=tid, tokens_used=200, model="gpt-4o-mini")

    usage = get_monthly_llm_usage()
    assert usage["tokens_used"] == 300

    tenant_usage = get_monthly_llm_usage(tenant_id=tid)
    assert tenant_usage["tokens_used"] == 300


# ---------------------------------------------------------------------------
# 3) Tenant isolation
# ---------------------------------------------------------------------------


def test_tenant_isolation():
    """One tenant's usage does not affect another tenant's view."""
    tid_a = str(uuid.uuid4())
    tid_b = str(uuid.uuid4())

    record_llm_usage(tenant_id=tid_a, tokens_used=500, model="gpt-4o-mini")
    record_llm_usage(tenant_id=tid_b, tokens_used=200, model="gpt-4o-mini")

    usage_a = get_monthly_llm_usage(tenant_id=tid_a)
    usage_b = get_monthly_llm_usage(tenant_id=tid_b)

    assert usage_a["tokens_used"] == 500
    assert usage_b["tokens_used"] == 200

    # Global total includes both
    global_usage = get_monthly_llm_usage()
    assert global_usage["tokens_used"] == 700
    assert global_usage["by_tenant"][tid_a] == 500
    assert global_usage["by_tenant"][tid_b] == 200


# ---------------------------------------------------------------------------
# 4) check_llm_limit — unlimited
# ---------------------------------------------------------------------------


def test_check_llm_limit_unlimited(monkeypatch):
    """limit=0 means unlimited — should not raise."""
    monkeypatch.delenv("OPENAI_MONTHLY_TOKEN_LIMIT", raising=False)
    get_settings.cache_clear()
    try:
        # Record a huge number of tokens
        record_llm_usage(tenant_id="t1", tokens_used=999_999_999, model="gpt-4o-mini")
        # Should not raise
        check_llm_limit()
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 5) check_llm_limit — exceeded
# ---------------------------------------------------------------------------


def test_check_llm_limit_exceeded(monkeypatch):
    """Raises ValueError when monthly limit is exceeded."""
    monkeypatch.setenv("OPENAI_MONTHLY_TOKEN_LIMIT", "100")
    get_settings.cache_clear()
    try:
        record_llm_usage(tenant_id="t1", tokens_used=150, model="gpt-4o-mini")
        with pytest.raises(ValueError, match="Monthly LLM token limit exceeded"):
            check_llm_limit()
    finally:
        monkeypatch.delenv("OPENAI_MONTHLY_TOKEN_LIMIT", raising=False)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 6) tracked_llm_call — rejected when limit exceeded
# ---------------------------------------------------------------------------


def test_tracked_llm_call_with_limit(monkeypatch):
    """tracked_llm_call returns None when limit is exceeded."""
    monkeypatch.setenv("OPENAI_MONTHLY_TOKEN_LIMIT", "50")
    get_settings.cache_clear()
    try:
        record_llm_usage(tenant_id="t1", tokens_used=100, model="gpt-4o-mini")

        def fake_fn(prompt, key, model):
            return "should not be called"

        result = tracked_llm_call(
            prompt="test",
            api_key="sk-test",
            model="gpt-4o-mini",
            tenant_id="t1",
            original_fn=fake_fn,
        )
        assert result is None
    finally:
        monkeypatch.delenv("OPENAI_MONTHLY_TOKEN_LIMIT", raising=False)
        get_settings.cache_clear()


def test_tracked_llm_call_success():
    """tracked_llm_call records usage on success."""

    def fake_fn(prompt, key, model):
        return "Hello from LLM!"

    result = tracked_llm_call(
        prompt="Say hello",
        api_key="sk-test",
        model="gpt-4o-mini",
        tenant_id="tenant-1",
        original_fn=fake_fn,
    )
    assert result == "Hello from LLM!"

    usage = get_monthly_llm_usage(tenant_id="tenant-1")
    assert usage["tokens_used"] > 0


# ---------------------------------------------------------------------------
# 7) _default_llm_call accepts tenant_id (backward compatible)
# ---------------------------------------------------------------------------


def test_default_llm_call_accepts_tenant_id():
    """_default_llm_call signature accepts tenant_id as keyword-only arg."""
    import inspect
    from domain.invoice_ean import _default_llm_call

    sig = inspect.signature(_default_llm_call)
    params = list(sig.parameters.keys())
    # Original 3 positional params must still exist
    assert "prompt" in params
    assert "api_key" in params
    assert "model" in params
    # tenant_id must be keyword-only
    assert "tenant_id" in params
    param = sig.parameters["tenant_id"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


# ---------------------------------------------------------------------------
# 8) Config field exists
# ---------------------------------------------------------------------------


def test_config_has_monthly_limit_field(monkeypatch):
    """Settings class has openai_monthly_token_limit with default 0."""
    monkeypatch.delenv("OPENAI_MONTHLY_TOKEN_LIMIT", raising=False)
    get_settings.cache_clear()
    try:
        s = Settings()
        assert hasattr(s, "openai_monthly_token_limit")
        assert s.openai_monthly_token_limit == 0

        # Also verify it appears in safe dict (not a secret)
        safe = s.to_safe_dict()
        assert "openai_monthly_token_limit" in safe
        assert safe["openai_monthly_token_limit"] == 0
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 9) Admin endpoint includes LLM usage
# ---------------------------------------------------------------------------


def test_admin_endpoint_includes_llm_usage(monkeypatch):
    """Admin tenant detail response model includes llm_usage field."""
    from backend.admin_api import TenantDetailResponse

    # Verify the model has the field
    fields = TenantDetailResponse.model_fields
    assert "llm_usage" in fields

    # Verify we can construct a response with llm_usage
    resp = TenantDetailResponse(
        id="test-id",
        name="Test Tenant",
        limits={"optimizations_per_day": 10},
        usage={"optimizations_today": 0},
        llm_usage={"month": "2026-04", "tokens_used": 42},
    )
    data = resp.model_dump()
    assert data["llm_usage"]["tokens_used"] == 42

    # Verify None is also valid
    resp_none = TenantDetailResponse(
        id="test-id",
        name="Test Tenant",
        limits={},
        usage={},
        llm_usage=None,
    )
    assert resp_none.llm_usage is None
