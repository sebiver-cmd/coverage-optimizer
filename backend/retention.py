"""Data retention — prune old records from the database (Task 10.2).

Provides per-table pruning functions and a top-level :func:`run_retention`
that applies configurable cutoffs and emits a single audit event.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models import ApplyBatch, AuditEvent, OptimizationJob

logger = logging.getLogger(__name__)


def prune_jobs(db: Session, cutoff_dt: datetime) -> int:
    """Delete ``optimization_jobs`` with ``created_at < cutoff_dt``.

    Returns the number of rows deleted.
    """
    count = (
        db.query(OptimizationJob)
        .filter(OptimizationJob.created_at < cutoff_dt)
        .delete(synchronize_session="fetch")
    )
    return count


def prune_batches(db: Session, cutoff_dt: datetime) -> int:
    """Delete ``apply_batches`` with ``created_at < cutoff_dt``.

    Returns the number of rows deleted.
    """
    count = (
        db.query(ApplyBatch)
        .filter(ApplyBatch.created_at < cutoff_dt)
        .delete(synchronize_session="fetch")
    )
    return count


def prune_audit(db: Session, cutoff_dt: datetime) -> int:
    """Delete ``audit_events`` with ``created_at < cutoff_dt``.

    Returns the number of rows deleted.
    """
    count = (
        db.query(AuditEvent)
        .filter(AuditEvent.created_at < cutoff_dt)
        .delete(synchronize_session="fetch")
    )
    return count


def run_retention(
    db: Session,
    settings,
    now_utc: datetime | None = None,
) -> dict:
    """Execute retention pruning according to *settings*.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    settings:
        Application :class:`~backend.config.Settings` (must have
        ``retention_enabled``, ``retention_jobs_days``,
        ``retention_batches_days``, ``retention_audit_days``).
    now_utc:
        Reference timestamp; defaults to ``datetime.now(UTC)``.

    Returns
    -------
    A dict with ``cutoffs`` and ``pruned_counts``.
    """
    if not settings.retention_enabled:
        logger.info("Retention disabled — skipping.")
        return {"cutoffs": {}, "pruned_counts": {}}

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    cutoff_jobs = now_utc - timedelta(days=settings.retention_jobs_days)
    cutoff_batches = now_utc - timedelta(days=settings.retention_batches_days)
    cutoff_audit = now_utc - timedelta(days=settings.retention_audit_days)

    jobs_deleted = prune_jobs(db, cutoff_jobs)
    batches_deleted = prune_batches(db, cutoff_batches)
    audit_deleted = prune_audit(db, cutoff_audit)

    db.commit()

    cutoffs = {
        "jobs": cutoff_jobs.isoformat(),
        "batches": cutoff_batches.isoformat(),
        "audit": cutoff_audit.isoformat(),
    }
    pruned_counts = {
        "jobs": jobs_deleted,
        "batches": batches_deleted,
        "audit": audit_deleted,
    }

    # Emit a single maintenance audit event (no tenant scope — admin-level).
    db.add(
        AuditEvent(
            id=uuid.uuid4(),
            tenant_id=None,
            user_id=None,
            event_type="maintenance.retention_ran",
            created_at=now_utc,
            meta_json=json.dumps({"cutoffs": cutoffs, "pruned_counts": pruned_counts}),
        )
    )
    db.commit()

    logger.info(
        "Retention complete: pruned_counts=%s cutoffs=%s",
        pruned_counts,
        cutoffs,
    )

    return {"cutoffs": cutoffs, "pruned_counts": pruned_counts}
