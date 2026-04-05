"""Repository for OptimizationJob persistence (Task 6.1).

All functions accept a SQLAlchemy session and enforce tenant scoping.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.models import AuditEvent, OptimizationJob


def create_job(
    db: Session,
    *,
    job_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    request_meta: dict | None = None,
) -> OptimizationJob:
    """Insert a new OptimizationJob row with status *queued*."""
    job = OptimizationJob(
        id=job_id or uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        status="queued",
        created_at=datetime.now(timezone.utc),
        request_json=json.dumps(request_meta) if request_meta else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_job_status(
    db: Session,
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    result: Any = None,
    error: str | None = None,
) -> OptimizationJob | None:
    """Update an existing job row.  Returns None if not found."""
    q = db.query(OptimizationJob).filter(OptimizationJob.id == job_id)
    if tenant_id is not None:
        q = q.filter(OptimizationJob.tenant_id == tenant_id)
    job = q.first()
    if job is None:
        return None
    job.status = status
    if started_at is not None:
        job.started_at = started_at
    if finished_at is not None:
        job.finished_at = finished_at
    if result is not None:
        job.result_json = json.dumps(result)
    if error is not None:
        job.error = error
    db.commit()
    db.refresh(job)
    return job


def get_job(
    db: Session,
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> OptimizationJob | None:
    """Fetch a single job scoped to *tenant_id*."""
    return (
        db.query(OptimizationJob)
        .filter(
            OptimizationJob.id == job_id,
            OptimizationJob.tenant_id == tenant_id,
        )
        .first()
    )


def list_jobs(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[OptimizationJob]:
    """List jobs for a tenant, newest first."""
    return (
        db.query(OptimizationJob)
        .filter(OptimizationJob.tenant_id == tenant_id)
        .order_by(OptimizationJob.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def emit_job_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    event_type: str,
    meta: dict | None = None,
) -> None:
    """Write an audit event for a job action."""
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
