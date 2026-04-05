"""Lightweight Prometheus metrics for SB-Optima (Task 9.1).

Metrics are only collected (and the ``/metrics`` endpoint mounted) when
the ``METRICS_ENABLED`` environment variable / config flag is ``True``.

Provides:
- Pre-defined Prometheus counters and histograms.
- :func:`get_metrics_router` — returns a FastAPI ``APIRouter`` with the
  ``/metrics`` endpoint (admin+ when auth is required).
- Helper functions to record domain events from callsites.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom registry — keeps our metrics isolated from process-level
# collectors that prometheus_client registers by default.
# ---------------------------------------------------------------------------

REGISTRY = CollectorRegistry()

# ---------------------------------------------------------------------------
# HTTP metrics
# ---------------------------------------------------------------------------

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Domain metrics
# ---------------------------------------------------------------------------

SOAP_CALLS_TOTAL = Counter(
    "soap_calls_total",
    "Total SOAP calls to DanDomain",
    registry=REGISTRY,
)

QUOTA_EXCEEDED_TOTAL = Counter(
    "quota_exceeded_total",
    "Total quota-exceeded blocks",
    ["action"],
    registry=REGISTRY,
)

BILLING_WEBHOOK_EVENTS_TOTAL = Counter(
    "billing_webhook_events_total",
    "Total Stripe billing webhook events processed",
    ["type"],
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Recording helpers (safe to call even when metrics are conceptually
# disabled — counters are always in-memory, but the /metrics endpoint
# simply won't be mounted).
# ---------------------------------------------------------------------------


def record_http_request(method: str, path: str, status: int, duration_s: float) -> None:
    """Increment HTTP counters for a completed request."""
    HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=str(status)).inc()
    HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration_s)


def record_soap_call() -> None:
    """Increment the SOAP call counter."""
    SOAP_CALLS_TOTAL.inc()


def record_quota_exceeded(action: str) -> None:
    """Increment the quota-exceeded counter for *action*."""
    QUOTA_EXCEEDED_TOTAL.labels(action=action).inc()


def record_billing_webhook(event_type: str) -> None:
    """Increment the billing webhook counter for *event_type*."""
    BILLING_WEBHOOK_EVENTS_TOTAL.labels(type=event_type).inc()


# ---------------------------------------------------------------------------
# /metrics endpoint (Prometheus exposition format)
# ---------------------------------------------------------------------------


def get_metrics_router():
    """Return a FastAPI router exposing ``GET /metrics``.

    The endpoint is guarded by admin+ RBAC when auth is enabled.
    When ``METRICS_ENABLED`` is False the endpoint returns 503.
    """
    from fastapi import APIRouter, Depends, Response
    from backend.config import get_settings
    from backend.rbac import require_role

    router = APIRouter(tags=["observability"])

    @router.get("/metrics", dependencies=[Depends(require_role("admin"))])
    def metrics_endpoint() -> Response:
        settings = get_settings()
        if not settings.metrics_enabled:
            return Response(status_code=503, content="Metrics are disabled.")
        body = generate_latest(REGISTRY)
        return Response(content=body, media_type=CONTENT_TYPE_LATEST)

    return router
