"""Admin diagnostics API for SB-Optima (Tasks 9.2 & 10.2).

Provides admin-only endpoints that expose operational information without
leaking secrets or PII:

- ``GET /admin/diagnostics`` — app health, config flags, DB latency, counts.
- ``GET /admin/tenants``     — paginated tenant list (metadata only).
- ``GET /admin/tenant/{tenant_id}`` — single tenant detail + limits + usage.
- ``GET /admin/tenant/{tenant_id}/export`` — tenant data export (metadata only).

All endpoints require ``admin+`` role when auth is enabled.
When auth is disabled (``SBOPTIMA_AUTH_REQUIRED=false``), all endpoints
return **503** to prevent accidental exposure.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from backend.config import get_settings
from backend.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _admin_unavailable(reason: str = "Admin endpoints require auth.") -> HTTPException:
    return HTTPException(status_code=503, detail=reason)


def _require_auth_enabled() -> None:
    """Raise 503 when auth is not enabled — admin endpoints must not be
    accessible in legacy / open mode."""
    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _admin_unavailable(
            "Admin diagnostics require auth — set SBOPTIMA_AUTH_REQUIRED=true."
        )


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class DiagnosticsResponse(BaseModel):
    """Response for ``GET /admin/diagnostics``."""

    version: str
    git_sha: Optional[str] = None
    config_flags: dict[str, bool]
    db_status: str
    db_latency_ms: Optional[float] = None
    counts: dict[str, int]


class TenantListItem(BaseModel):
    """Tenant metadata returned in list endpoints (no PII)."""

    id: str
    name: str
    plan: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    billing_status: Optional[str] = None


class TenantListResponse(BaseModel):
    """Paginated tenant list."""

    total: int
    items: list[TenantListItem]


class TenantDetailResponse(BaseModel):
    """Detailed tenant view including limits and usage snapshot."""

    id: str
    name: str
    plan: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    billing_status: Optional[str] = None
    has_stripe_customer: bool = False
    has_stripe_subscription: bool = False
    limits: dict[str, Optional[int]]
    usage: dict[str, int]
    user_count: int = 0
    credential_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_db_session(request: Request):
    """Resolve a DB session from dependency overrides or the real ``get_db``."""
    from backend.db import get_db

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    return db, db_gen


def _close_db(db_gen):
    """Safely close a DB generator."""
    try:
        next(db_gen, None)
    except StopIteration:
        pass


def _safe_isoformat(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


# ---------------------------------------------------------------------------
# GET /admin/diagnostics
# ---------------------------------------------------------------------------


@router.get(
    "/diagnostics",
    response_model=DiagnosticsResponse,
    dependencies=[Depends(require_role("admin"))],
)
def admin_diagnostics(request: Request) -> DiagnosticsResponse:
    """App health, config flags, DB connectivity + latency, and entity counts.

    Does **not** expose secrets, tokens, or PII.
    """
    _require_auth_enabled()

    settings = get_settings()

    # Config flags (booleans only — safe to expose)
    config_flags = {
        "auth_required": settings.sboptima_auth_required,
        "billing_enabled": settings.billing_enabled,
        "metrics_enabled": settings.metrics_enabled,
    }

    # DB probe with latency
    from backend.db import get_engine
    from sqlalchemy import text

    engine = get_engine()
    db_status = "skipped"
    db_latency_ms: float | None = None
    counts: dict[str, int] = {"tenants": 0, "users": 0, "jobs": 0}

    if engine is not None:
        try:
            t0 = time.monotonic()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_latency_ms = round((time.monotonic() - t0) * 1000, 2)
            db_status = "ok"
        except Exception:
            db_status = "error"

    # Counts via the session dependency (works with overrides in tests)
    try:
        db, db_gen = _get_db_session(request)
        try:
            from backend.models import OptimizationJob, Tenant, User

            counts["tenants"] = db.query(Tenant).count()
            counts["users"] = db.query(User).count()
            counts["jobs"] = db.query(OptimizationJob).count()
            # If engine probe was skipped, mark db as ok based on session
            if db_status == "skipped":
                db_status = "ok"
        except Exception:
            pass
        finally:
            _close_db(db_gen)
    except Exception:
        pass

    return DiagnosticsResponse(
        version="0.1.0",
        git_sha=os.environ.get("GIT_SHA"),
        config_flags=config_flags,
        db_status=db_status,
        db_latency_ms=db_latency_ms,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# GET /admin/tenants
# ---------------------------------------------------------------------------


@router.get(
    "/tenants",
    response_model=TenantListResponse,
    dependencies=[Depends(require_role("admin"))],
)
def admin_list_tenants(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    plan: Optional[str] = Query(default=None),
) -> TenantListResponse:
    """Paginated list of tenants (metadata only, no PII)."""
    _require_auth_enabled()

    db, db_gen = _get_db_session(request)
    try:
        from backend.models import Tenant

        query = db.query(Tenant)
        if plan:
            query = query.filter(Tenant.plan == plan)

        total = query.count()
        tenants = query.order_by(Tenant.created_at.desc()).offset(offset).limit(limit).all()

        items = [
            TenantListItem(
                id=str(t.id),
                name=t.name,
                plan=t.plan,
                status=t.status,
                created_at=_safe_isoformat(t.created_at),
                billing_status=t.billing_status,
            )
            for t in tenants
        ]

        return TenantListResponse(total=total, items=items)
    finally:
        _close_db(db_gen)


# ---------------------------------------------------------------------------
# GET /admin/tenant/{tenant_id}
# ---------------------------------------------------------------------------


@router.get(
    "/tenant/{tenant_id}",
    response_model=TenantDetailResponse,
    dependencies=[Depends(require_role("admin"))],
)
def admin_get_tenant(
    tenant_id: uuid.UUID,
    request: Request,
) -> TenantDetailResponse:
    """Detailed view for a single tenant: metadata, limits, usage, billing booleans."""
    _require_auth_enabled()

    db, db_gen = _get_db_session(request)
    try:
        from backend.models import HostedShopCredential, Tenant, User
        from backend.quotas import get_day_window, get_limits, get_usage

        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        # Limits and today's usage
        limits = get_limits(tenant)
        now_utc = datetime.now(timezone.utc)
        day_start, day_end = get_day_window(now_utc)
        usage = get_usage(db, tenant.id, day_start, day_end)

        user_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        cred_count = (
            db.query(HostedShopCredential)
            .filter(HostedShopCredential.tenant_id == tenant.id)
            .count()
        )

        return TenantDetailResponse(
            id=str(tenant.id),
            name=tenant.name,
            plan=tenant.plan,
            status=tenant.status,
            created_at=_safe_isoformat(tenant.created_at),
            billing_status=tenant.billing_status,
            has_stripe_customer=bool(tenant.stripe_customer_id),
            has_stripe_subscription=bool(tenant.stripe_subscription_id),
            limits=limits,
            usage=usage,
            user_count=user_count,
            credential_count=cred_count,
        )
    finally:
        _close_db(db_gen)


# ---------------------------------------------------------------------------
# GET /admin/tenant/{tenant_id}/export  (Task 10.2)
# ---------------------------------------------------------------------------

#: Maximum rows per collection in a tenant export (cap for safety).
_EXPORT_CAP = 10_000

#: Fields that must NEVER appear in an export payload.
_EXPORT_REDACT_FIELDS = frozenset(
    {
        "password_hash",
        "api_username_enc",
        "api_password_enc",
        "encryption_key",
        "stripe_secret_key",
        "stripe_webhook_secret",
    }
)


def _safe_export_row(row: Any) -> dict:
    """Convert a model instance to a JSON-safe dict, redacting secret fields."""
    d: dict[str, Any] = {}
    for col in row.__table__.columns:
        key = col.name
        if key in _EXPORT_REDACT_FIELDS:
            continue
        val = getattr(row, key, None)
        if isinstance(val, uuid.UUID):
            val = str(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        d[key] = val
    return d


@router.get(
    "/tenant/{tenant_id}/export",
    dependencies=[Depends(require_role("admin"))],
)
def admin_export_tenant(
    tenant_id: uuid.UUID,
    request: Request,
) -> dict[str, Any]:
    """Export a tenant's data (metadata only) as a JSON bundle.

    Returns tenant info, users (no password hashes, no email/PII), jobs,
    batches, and audit events.  Credentials are excluded entirely.  Each
    collection is capped at 10 000 rows; if truncated a ``truncated`` flag
    is set.
    """
    _require_auth_enabled()

    db, db_gen = _get_db_session(request)
    try:
        from backend.models import (
            ApplyBatch,
            AuditEvent,
            OptimizationJob,
            Tenant,
            User,
        )

        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        # Tenant metadata (safe subset)
        tenant_data = {
            "id": str(tenant.id),
            "name": tenant.name,
            "plan": tenant.plan,
            "status": tenant.status,
            "billing_status": tenant.billing_status,
            "created_at": _safe_isoformat(tenant.created_at),
        }

        # Users — omit password_hash
        user_rows = (
            db.query(User)
            .filter(User.tenant_id == tenant_id)
            .order_by(User.created_at.desc())
            .limit(_EXPORT_CAP + 1)
            .all()
        )
        users_truncated = len(user_rows) > _EXPORT_CAP
        users = [
            {
                "id": str(u.id),
                "role": u.role.value if hasattr(u.role, "value") else u.role,
                "created_at": _safe_isoformat(u.created_at),
            }
            for u in user_rows[:_EXPORT_CAP]
        ]

        # Jobs
        job_rows = (
            db.query(OptimizationJob)
            .filter(OptimizationJob.tenant_id == tenant_id)
            .order_by(OptimizationJob.created_at.desc())
            .limit(_EXPORT_CAP + 1)
            .all()
        )
        jobs_truncated = len(job_rows) > _EXPORT_CAP
        jobs = [_safe_export_row(j) for j in job_rows[:_EXPORT_CAP]]

        # Batches
        batch_rows = (
            db.query(ApplyBatch)
            .filter(ApplyBatch.tenant_id == tenant_id)
            .order_by(ApplyBatch.created_at.desc())
            .limit(_EXPORT_CAP + 1)
            .all()
        )
        batches_truncated = len(batch_rows) > _EXPORT_CAP
        batches = [_safe_export_row(b) for b in batch_rows[:_EXPORT_CAP]]

        # Audit events
        audit_rows = (
            db.query(AuditEvent)
            .filter(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(_EXPORT_CAP + 1)
            .all()
        )
        audit_truncated = len(audit_rows) > _EXPORT_CAP
        audit = [_safe_export_row(a) for a in audit_rows[:_EXPORT_CAP]]

        # Emit audit event for the export action
        db.add(
            AuditEvent(
                tenant_id=tenant_id,
                user_id=None,
                event_type="admin.tenant.exported",
                created_at=datetime.now(timezone.utc),
                meta_json=json.dumps({"tenant_id": str(tenant_id)}),
            )
        )
        db.commit()

        result: dict[str, Any] = {
            "tenant": tenant_data,
            "users": users,
            "jobs": jobs,
            "batches": batches,
            "audit": audit,
        }
        # Add truncation flags
        if users_truncated:
            result["users_truncated"] = True
        if jobs_truncated:
            result["jobs_truncated"] = True
        if batches_truncated:
            result["batches_truncated"] = True
        if audit_truncated:
            result["audit_truncated"] = True

        return result
    finally:
        _close_db(db_gen)
