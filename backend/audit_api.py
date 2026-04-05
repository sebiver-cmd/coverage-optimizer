"""Audit event list endpoint (Task 6.2).

Provides ``GET /audit`` — a paginated, tenant-scoped list of audit events.
Requires ``SBOPTIMA_AUTH_REQUIRED=true``; returns 503 when auth is disabled.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AuditListItem(BaseModel):
    """Single item returned by ``GET /audit``."""

    id: str
    event_type: str
    created_at: Optional[str] = None
    user_id: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class AuditListResponse(BaseModel):
    """Paginated list returned by ``GET /audit``."""

    total: int
    items: list[AuditListItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_META_MAX_LEN = 2048  # truncate meta_json display beyond this


def _history_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="History requires auth — set SBOPTIMA_AUTH_REQUIRED=true.",
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _safe_meta(raw: str | None) -> dict[str, Any] | None:
    """Parse *meta_json* and truncate large blobs."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return {"_value": str(data)[:_META_MAX_LEN]}
    # Truncate overly large string values inside the dict
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > _META_MAX_LEN:
            out[k] = v[:_META_MAX_LEN] + "…"
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditListResponse, dependencies=[Depends(require_role("viewer"))])
def list_audit_endpoint(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> AuditListResponse:
    """Return a paginated, tenant-scoped list of audit events.

    Only available when ``SBOPTIMA_AUTH_REQUIRED=true``.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _history_unavailable()

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _history_unavailable()

    from backend.db import get_db
    from backend.repositories import audit_repo

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        total, items = audit_repo.list_audit_events(
            db,
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            event_type=event_type,
            since=since_dt,
            until=until_dt,
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass

    return AuditListResponse(
        total=total,
        items=[
            AuditListItem(
                id=str(e.id),
                event_type=e.event_type,
                created_at=e.created_at.isoformat() if e.created_at else None,
                user_id=str(e.user_id) if e.user_id else None,
                meta=_safe_meta(e.meta_json),
            )
            for e in items
        ],
    )
