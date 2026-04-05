"""Repository for UsageEvent persistence (Task 7.2).

All functions accept a SQLAlchemy session and enforce tenant scoping.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import UsageEvent


def emit_usage_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    meta: dict[str, Any] | None = None,
) -> UsageEvent:
    """Create a UsageEvent row for billing/metering."""
    evt = UsageEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        created_at=datetime.now(timezone.utc),
        meta_json=json.dumps(meta) if meta else None,
    )
    db.add(evt)
    db.commit()
    db.refresh(evt)
    return evt


def list_usage_events(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[int, list[UsageEvent]]:
    """List usage events for a tenant, newest first, with optional filters.

    Returns ``(total_count, items)`` where *total_count* is the number
    of rows matching the filters (before limit/offset).
    """
    q = db.query(UsageEvent).filter(UsageEvent.tenant_id == tenant_id)
    if event_type is not None:
        q = q.filter(UsageEvent.event_type == event_type)
    if since is not None:
        q = q.filter(UsageEvent.created_at >= since)
    if until is not None:
        q = q.filter(UsageEvent.created_at <= until)
    total = q.count()
    items = (
        q.order_by(UsageEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, items
