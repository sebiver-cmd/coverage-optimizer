"""Repository for AuditEvent queries (Task 6.2).

All functions accept a SQLAlchemy session and enforce tenant scoping.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from backend.models import AuditEvent


def list_audit_events(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[int, list[AuditEvent]]:
    """List audit events for a tenant, newest first, with optional filters.

    Returns ``(total_count, items)`` where *total_count* is the number
    of rows matching the filters (before limit/offset).
    """
    q = db.query(AuditEvent).filter(AuditEvent.tenant_id == tenant_id)
    if event_type is not None:
        q = q.filter(AuditEvent.event_type == event_type)
    if since is not None:
        q = q.filter(AuditEvent.created_at >= since)
    if until is not None:
        q = q.filter(AuditEvent.created_at <= until)
    total = q.count()
    items = (
        q.order_by(AuditEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, items
