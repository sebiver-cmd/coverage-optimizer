"""Repository for ApplyBatch persistence (Task 6.1).

All functions accept a SQLAlchemy session and enforce tenant scoping.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import ApplyBatch, AuditEvent


def create_batch(
    db: Session,
    *,
    batch_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    mode: str,
    manifest_meta: dict | None = None,
) -> ApplyBatch:
    """Insert a new ApplyBatch row with status *created*."""
    batch = ApplyBatch(
        id=batch_id or uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        mode=mode,
        status="created",
        created_at=datetime.now(timezone.utc),
        manifest_json=json.dumps(manifest_meta) if manifest_meta else None,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def update_batch_status(
    db: Session,
    *,
    batch_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    summary: dict | None = None,
    error: str | None = None,
) -> ApplyBatch | None:
    """Update an existing batch row.  Returns None if not found."""
    q = db.query(ApplyBatch).filter(ApplyBatch.id == batch_id)
    if tenant_id is not None:
        q = q.filter(ApplyBatch.tenant_id == tenant_id)
    batch = q.first()
    if batch is None:
        return None
    batch.status = status
    if started_at is not None:
        batch.started_at = started_at
    if finished_at is not None:
        batch.finished_at = finished_at
    if summary is not None:
        batch.summary_json = json.dumps(summary)
    if error is not None:
        batch.error = error
    db.commit()
    db.refresh(batch)
    return batch


def get_batch(
    db: Session,
    *,
    batch_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> ApplyBatch | None:
    """Fetch a single batch scoped to *tenant_id*."""
    return (
        db.query(ApplyBatch)
        .filter(
            ApplyBatch.id == batch_id,
            ApplyBatch.tenant_id == tenant_id,
        )
        .first()
    )


def list_batches(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[ApplyBatch]:
    """List batches for a tenant, newest first."""
    return (
        db.query(ApplyBatch)
        .filter(ApplyBatch.tenant_id == tenant_id)
        .order_by(ApplyBatch.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def emit_batch_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    event_type: str,
    meta: dict | None = None,
) -> None:
    """Write an audit event for a batch action."""
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            user_id=user_id,
            event_type=event_type,
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps(meta) if meta else None,
        )
    )
    db.commit()
