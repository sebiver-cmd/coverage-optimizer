"""Usage-limit / quota enforcement for SB-Optima (Task 7.1).

Provides:
- :func:`get_day_window` — compute the UTC day window for a given instant.
- :func:`get_usage` — count today's usage from DB tables.
- :func:`get_limits` — extract limit settings from a Tenant.
- :func:`check_quota` — raise :class:`QuotaExceeded` (HTTP 429) when a
  tenant has hit their daily cap for a given action.

Quotas are only enforced when ``SBOPTIMA_AUTH_REQUIRED=true``.  In legacy
mode the functions are never called.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from backend.models import ApplyBatch, AuditEvent, OptimizationJob, Tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported quota actions
# ---------------------------------------------------------------------------

ACTIONS = ("optimize_job", "apply", "optimize_sync")


# ---------------------------------------------------------------------------
# QuotaExceeded exception  (wraps HTTP 429)
# ---------------------------------------------------------------------------


class QuotaExceeded(HTTPException):
    """Raised when a tenant exceeds a daily usage limit."""

    def __init__(self, *, action: str, used: int, limit: int, reset_at: str):
        detail = (
            f"Daily limit reached for {action} "
            f"(used {used} / limit {limit}). "
            f"Try again after {reset_at}."
        )
        super().__init__(status_code=429, detail=detail)
        self.action = action
        self.used = used
        self.limit = limit
        self.reset_at = reset_at


# ---------------------------------------------------------------------------
# Day window
# ---------------------------------------------------------------------------


def get_day_window(now_utc: datetime) -> tuple[datetime, datetime]:
    """Return ``(day_start, day_end)`` for the UTC calendar day of *now_utc*.

    Both boundaries are timezone-aware (UTC).  The window is
    ``[day_start, day_end)`` — inclusive start, exclusive end.
    """
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_start.tzinfo is None:
        day_start = day_start.replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


# ---------------------------------------------------------------------------
# Usage counting
# ---------------------------------------------------------------------------


def get_usage(
    db: Session,
    tenant_id: uuid.UUID,
    day_start: datetime,
    day_end: datetime,
) -> dict[str, int]:
    """Count today's usage from persisted DB rows.

    Returns a dict with keys ``optimize_job``, ``apply``, ``optimize_sync``.
    """
    optimize_job_count = (
        db.query(OptimizationJob)
        .filter(
            OptimizationJob.tenant_id == tenant_id,
            OptimizationJob.created_at >= day_start,
            OptimizationJob.created_at < day_end,
        )
        .count()
    )

    apply_count = (
        db.query(ApplyBatch)
        .filter(
            ApplyBatch.tenant_id == tenant_id,
            ApplyBatch.mode == "apply",
            ApplyBatch.created_at >= day_start,
            ApplyBatch.created_at < day_end,
        )
        .count()
    )

    # optimize_sync: count audit events with type "optimize_sync" for today
    # (sync optimize doesn't create OptimizationJob rows — it's in-request)
    optimize_sync_count = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.event_type == "optimize_sync",
            AuditEvent.created_at >= day_start,
            AuditEvent.created_at < day_end,
        )
        .count()
    )

    return {
        "optimize_job": optimize_job_count,
        "apply": apply_count,
        "optimize_sync": optimize_sync_count,
    }


# ---------------------------------------------------------------------------
# Limits from tenant
# ---------------------------------------------------------------------------


def get_limits(tenant: Tenant) -> dict[str, int | None]:
    """Extract daily limit values from *tenant*.

    ``None`` means unlimited.
    """
    return {
        "optimize_job": tenant.daily_optimize_jobs_limit,
        "apply": tenant.daily_apply_limit,
        "optimize_sync": tenant.daily_optimize_sync_limit,
    }


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def check_quota(
    db: Session,
    tenant: Tenant,
    action: str,
    now_utc: datetime | None = None,
) -> None:
    """Check and enforce daily quota for *action*.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    tenant:
        The :class:`Tenant` whose limits to check.
    action:
        One of ``"optimize_job"``, ``"apply"``, ``"optimize_sync"``.
    now_utc:
        The current UTC time.  Defaults to ``datetime.now(timezone.utc)``.

    Raises
    ------
    QuotaExceeded
        When the tenant has reached their daily limit for *action*.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown quota action: {action!r}")

    limits = get_limits(tenant)
    limit = limits.get(action)
    if limit is None:
        return  # unlimited

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    day_start, day_end = get_day_window(now_utc)
    usage = get_usage(db, tenant.id, day_start, day_end)
    used = usage.get(action, 0)

    if used >= limit:
        reset_at = day_end.isoformat()
        # Emit audit event
        _emit_quota_audit(
            db,
            tenant_id=tenant.id,
            action=action,
            used=used,
            limit=limit,
            reset_at=reset_at,
        )
        raise QuotaExceeded(
            action=action,
            used=used,
            limit=limit,
            reset_at=reset_at,
        )


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _emit_quota_audit(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    used: int,
    limit: int,
    reset_at: str,
) -> None:
    """Record a ``quota.exceeded`` audit event."""
    meta: dict[str, Any] = {
        "action": action,
        "used": used,
        "limit": limit,
        "reset_at": reset_at,
    }
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            user_id=None,
            event_type="quota.exceeded",
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps(meta),
        )
    )
    db.commit()
