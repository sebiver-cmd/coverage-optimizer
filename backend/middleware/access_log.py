"""Access-log middleware for SB-Optima (Task 9.1).

Emits one structured JSON log line per completed HTTP request with timing,
status, and correlation IDs (request_id, tenant_id, user_id).
Also records per-request Prometheus metrics.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.metrics import record_http_request

logger = logging.getLogger("sboptima.access")


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log every completed request with duration and context IDs."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.monotonic()

        try:
            response = await call_next(request)
        except Exception:
            duration_s = time.monotonic() - start
            duration_ms = round(duration_s * 1000, 2)
            record_http_request(request.method, request.url.path, 500, duration_s)
            logger.exception(
                "Unhandled exception",
                extra=_extra(request, status_code=500, duration_ms=duration_ms),
            )
            raise

        duration_s = time.monotonic() - start
        duration_ms = round(duration_s * 1000, 2)
        record_http_request(request.method, request.url.path, response.status_code, duration_s)
        logger.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra=_extra(request, status_code=response.status_code, duration_ms=duration_ms),
        )
        return response


def _extra(request: Request, *, status_code: int, duration_ms: float) -> dict:
    """Build the ``extra`` dict for the log record."""
    extra: dict = {
        "path": request.url.path,
        "method": request.method,
        "status_code": status_code,
        "duration_ms": duration_ms,
    }
    # Correlation IDs (set by RequestIDMiddleware / auth layer)
    for attr in ("request_id", "tenant_id"):
        val = getattr(request.state, attr, None)
        if val is not None:
            extra[attr] = str(val)
    user = getattr(request.state, "user", None)
    if user is not None:
        extra["user_id"] = str(user.id)
    return extra
