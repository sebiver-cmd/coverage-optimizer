"""Tenant plan management API (Task 7.2).

Endpoints:
- ``GET  /plans``        — list available plans and their default limits.
- ``GET  /tenant/plan``  — current tenant plan + effective limits.
- ``PUT  /tenant/plan``  — update tenant plan (admin/owner only).

All endpoints require ``SBOPTIMA_AUTH_REQUIRED=true``; they return 503
when auth is disabled (legacy mode).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.plans import PLANS, Plan, get_plan, list_plans
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PlanInfo(BaseModel):
    """A single plan definition."""

    name: str
    daily_optimize_jobs_limit: Optional[int] = None
    daily_apply_limit: Optional[int] = None
    daily_optimize_sync_limit: Optional[int] = None


class PlansListResponse(BaseModel):
    """Response for ``GET /plans``."""

    plans: list[PlanInfo]


class TenantPlanResponse(BaseModel):
    """Response for ``GET /tenant/plan`` and ``PUT /tenant/plan``."""

    plan: Optional[str] = None
    effective_limits: dict[str, Optional[int]]


class UpdatePlanRequest(BaseModel):
    """Body for ``PUT /tenant/plan``."""

    plan: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plan_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Plan management requires auth — set SBOPTIMA_AUTH_REQUIRED=true.",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["plans"])


@router.get(
    "/plans",
    response_model=PlansListResponse,
    dependencies=[Depends(require_role("viewer"))],
)
def list_plans_endpoint(request: Request) -> PlansListResponse:
    """Return all available plans and their default limits."""
    from backend.config import get_settings

    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _plan_unavailable()

    plans = list_plans()
    return PlansListResponse(
        plans=[
            PlanInfo(
                name=p.name,
                daily_optimize_jobs_limit=p.daily_optimize_jobs_limit,
                daily_apply_limit=p.daily_apply_limit,
                daily_optimize_sync_limit=p.daily_optimize_sync_limit,
            )
            for p in plans
        ]
    )


@router.get(
    "/tenant/plan",
    response_model=TenantPlanResponse,
    dependencies=[Depends(require_role("viewer"))],
)
def get_tenant_plan(request: Request) -> TenantPlanResponse:
    """Return the current tenant's plan and effective limits."""
    from backend.config import get_settings

    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _plan_unavailable()

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _plan_unavailable()

    from backend.db import get_db
    from backend.models import Tenant
    from backend.quotas import get_limits

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise _plan_unavailable()

        return TenantPlanResponse(
            plan=tenant.plan,
            effective_limits=get_limits(tenant),
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass


@router.put(
    "/tenant/plan",
    response_model=TenantPlanResponse,
    dependencies=[Depends(require_role("admin"))],
)
def update_tenant_plan(
    body: UpdatePlanRequest,
    request: Request,
) -> TenantPlanResponse:
    """Update the current tenant's plan (admin/owner only).

    Emits a ``tenant.plan.updated`` audit event.
    """
    from backend.config import get_settings

    settings = get_settings()
    if not settings.sboptima_auth_required:
        raise _plan_unavailable()

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _plan_unavailable()

    # Validate plan value
    new_plan = body.plan
    if new_plan is not None:
        new_plan_lower = new_plan.lower()
        if new_plan_lower not in PLANS:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown plan: {new_plan!r}. Valid plans: {list(PLANS.keys())}",
            )
        new_plan = new_plan_lower

    from backend.db import get_db
    from backend.models import AuditEvent, Tenant
    from backend.quotas import get_limits

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise _plan_unavailable()

        old_plan = tenant.plan
        tenant.plan = new_plan
        db.flush()

        # Emit audit event
        user = getattr(request.state, "user", None)
        user_id = user.id if user else None
        db.add(
            AuditEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                event_type="tenant.plan.updated",
                created_at=datetime.now(timezone.utc),
                meta_json=json.dumps({"old": old_plan, "new": new_plan}),
            )
        )
        db.commit()
        db.refresh(tenant)

        return TenantPlanResponse(
            plan=tenant.plan,
            effective_limits=get_limits(tenant),
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass
