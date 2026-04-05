"""Request-size-limit middleware for SB-Optima (Task 10.1).

Rejects incoming requests whose ``Content-Length`` exceeds a configurable
maximum (default 1 MB) with HTTP **413 Content Too Large**.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.config import get_settings


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Return 413 when Content-Length exceeds the configured limit."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        settings = get_settings()
        max_bytes = settings.max_request_body_bytes

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                f"Request body too large. "
                                f"Maximum allowed size is {max_bytes} bytes."
                            )
                        },
                    )
            except (ValueError, TypeError):
                pass  # Non-numeric header — let the framework handle it.

        return await call_next(request)
