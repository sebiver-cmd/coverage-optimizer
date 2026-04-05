"""Tenant usage-status endpoint (Task 7.1).

Provides ``GET /usage`` — returns current daily usage vs limits for the
authenticated tenant.  Requires ``SBOPTIMA_AUTH_REQUIRED=true``; returns
503 when auth is disabled (legacy mode).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class UsageWindow(BaseModel):
    """UTC day window for the usage report."""

    start: str
    end: str


class UsageResponse(BaseModel):
    """Current daily usage vs limits for a tenant."""

    window: UsageWindow
    limits: dict[str, Optional[int]]
    used: dict[str, int]
    remaining: dict[str, Optional[int]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _usage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Usage requires auth — set SBOPTIMA_AUTH_REQUIRED=true.",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["usage"])


@router.get(
    "/usage",
    response_model=UsageResponse,
    dependencies=[Depends(require_role("viewer"))],
)
def get_usage_endpoint(request: Request) -> UsageResponse:
    """Return current daily usage vs limits for the authenticated tenant.

    Only available when ``SBOPTIMA_AUTH_REQUIRED=true``.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _usage_unavailable()

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _usage_unavailable()

    from backend.db import get_db
    from backend.models import Tenant
    from backend.quotas import get_day_window, get_limits, get_usage

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        now_utc = datetime.now(timezone.utc)
        day_start, day_end = get_day_window(now_utc)

        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise _usage_unavailable()

        limits = get_limits(tenant)
        used = get_usage(db, tenant_id, day_start, day_end)

        remaining: dict[str, int | None] = {}
        for key in limits:
            lim = limits[key]
            if lim is None:
                remaining[key] = None
            else:
                remaining[key] = max(0, lim - used.get(key, 0))

        return UsageResponse(
            window=UsageWindow(
                start=day_start.isoformat(),
                end=day_end.isoformat(),
            ),
            limits=limits,
            used=used,
            remaining=remaining,
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass
