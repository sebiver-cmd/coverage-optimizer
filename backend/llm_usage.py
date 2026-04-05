"""LLM usage tracking for SB-Optima (Task 5.3).

Provides:
- :func:`tracked_llm_call` — wraps an LLM call with tenant_id logging and token tracking.
- :func:`get_monthly_llm_usage` — get total tokens used this month.
- :func:`check_llm_limit` — raise error if monthly limit exceeded.

Note: The in-memory usage store is suitable for MVP / single-process
deployments.  Production should persist usage to the database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from backend.config import get_settings

logger = logging.getLogger(__name__)

# In-memory counter for token usage (per month key)
# Format: {"2026-04": {"total": 1234, "by_tenant": {"uuid1": 500, "uuid2": 734}}}
_usage_store: dict[str, dict[str, Any]] = {}


def _month_key(dt: datetime | None = None) -> str:
    """Return YYYY-MM key for the current or given datetime."""
    d = dt or datetime.now(timezone.utc)
    return d.strftime("%Y-%m")


def get_monthly_llm_usage(tenant_id: str | None = None) -> dict[str, Any]:
    """Return token usage for the current month.

    If tenant_id is provided, returns usage for that tenant only.
    Otherwise returns global totals.
    """
    key = _month_key()
    month_data = _usage_store.get(key, {"total": 0, "by_tenant": {}})
    if tenant_id:
        return {
            "month": key,
            "tokens_used": month_data["by_tenant"].get(tenant_id, 0),
        }
    return {
        "month": key,
        "tokens_used": month_data["total"],
        "by_tenant": dict(month_data.get("by_tenant", {})),
    }


def check_llm_limit() -> None:
    """Raise ValueError if the monthly token limit has been exceeded."""
    settings = get_settings()
    limit = settings.openai_monthly_token_limit
    if limit <= 0:
        return  # unlimited

    usage = get_monthly_llm_usage()
    if usage["tokens_used"] >= limit:
        raise ValueError(
            f"Monthly LLM token limit exceeded ({usage['tokens_used']}/{limit}). "
            f"Try again next month or increase OPENAI_MONTHLY_TOKEN_LIMIT."
        )


def record_llm_usage(
    tenant_id: str | None,
    tokens_used: int,
    model: str,
) -> None:
    """Record LLM token usage and emit structured log."""
    key = _month_key()
    if key not in _usage_store:
        _usage_store[key] = {"total": 0, "by_tenant": {}}

    _usage_store[key]["total"] += tokens_used

    if tenant_id:
        _usage_store[key]["by_tenant"][tenant_id] = (
            _usage_store[key]["by_tenant"].get(tenant_id, 0) + tokens_used
        )

    # Structured log entry for observability (Task 9.1 JSON logging)
    logger.info(
        "LLM usage recorded",
        extra={
            "tenant_id": tenant_id,
            "tokens_used": tokens_used,
            "model": model,
            "month": key,
            "monthly_total": _usage_store[key]["total"],
        },
    )


def tracked_llm_call(
    prompt: str,
    api_key: str,
    model: str,
    *,
    tenant_id: str | None = None,
    original_fn: Any = None,
) -> str | None:
    """Wrap an LLM call with tenant tracking, limit enforcement, and logging.

    Parameters
    ----------
    prompt : str
        The prompt to send to the LLM.
    api_key : str
        The API key for authentication.
    model : str
        The model name (e.g., 'gpt-4o-mini').
    tenant_id : str or None
        The tenant ID for usage tracking.
    original_fn : callable or None
        The original LLM call function.  If None, uses _default_llm_call
        from invoice_ean.
    """
    # Check monthly limit before making the call
    try:
        check_llm_limit()
    except ValueError as exc:
        logger.warning("LLM call rejected: %s", exc)
        return None

    # Make the actual LLM call
    if original_fn is None:
        from domain.invoice_ean import _default_llm_call
        original_fn = _default_llm_call

    result = original_fn(prompt, api_key, model)

    # Estimate token usage (rough: ~4 chars per token for prompt + response).
    # Actual OpenAI token counts may differ, especially for non-ASCII text.
    prompt_tokens = len(prompt) // 4
    response_tokens = len(result) // 4 if result else 0
    total_tokens = prompt_tokens + response_tokens

    # Record usage
    record_llm_usage(
        tenant_id=tenant_id,
        tokens_used=total_tokens,
        model=model,
    )

    return result


def reset_usage_store() -> None:
    """Reset the in-memory usage store (for testing)."""
    _usage_store.clear()
