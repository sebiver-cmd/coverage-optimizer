"""Minimal tenant + user CRUD API (Task 4.1).

**Temporary / internal** — no authentication or RBAC is enforced yet.
Auth will be added in Tasks 4.2 and 4.3.

Endpoints
---------
- ``POST /tenants``                     — create a new tenant.
- ``GET  /tenants/{tenant_id}``         — fetch a tenant by ID.
- ``POST /tenants/{tenant_id}/users``   — create a user under a tenant.
- ``GET  /tenants/{tenant_id}/users``   — list users for a tenant.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Role, Tenant, User
from backend.rbac import require_role

router = APIRouter(tags=["tenants"], dependencies=[Depends(require_role("admin"))])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TenantCreate(BaseModel):
    """Payload for creating a new tenant."""

    name: str = Field(..., min_length=1, max_length=255)
    plan: str | None = None
    status: str | None = None


class TenantOut(BaseModel):
    """Tenant response payload."""

    id: uuid.UUID
    name: str
    created_at: datetime
    stripe_customer_id: str | None = None
    plan: str | None = None
    status: str | None = None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    """Payload for creating a new user under a tenant."""

    email: EmailStr
    password_hash: str = Field(..., min_length=1)
    role: Role = Role.operator


class UserOut(BaseModel):
    """User response payload (never returns password_hash)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_tenant_or_404(tenant_id: uuid.UUID, db: Session) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


# ---------------------------------------------------------------------------
# Tenant endpoints
# ---------------------------------------------------------------------------


@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)):
    """Create a new tenant.  **No auth enforced yet** (Task 4.2)."""
    tenant = Tenant(
        name=payload.name,
        plan=payload.plan,
        status=payload.status,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
def get_tenant(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    """Fetch a single tenant by ID.  **No auth enforced yet** (Task 4.2)."""
    return _get_tenant_or_404(tenant_id, db)


# ---------------------------------------------------------------------------
# User endpoints (scoped under a tenant)
# ---------------------------------------------------------------------------


@router.post(
    "/tenants/{tenant_id}/users",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
def create_user(tenant_id: uuid.UUID, payload: UserCreate, db: Session = Depends(get_db)):
    """Create a new user under *tenant_id*.  **No auth enforced yet** (Task 4.2)."""
    _get_tenant_or_404(tenant_id, db)

    # Check for duplicate (tenant_id, email)
    existing = (
        db.query(User)
        .filter(User.tenant_id == tenant_id, User.email == payload.email)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists in this tenant",
        )

    user = User(
        tenant_id=tenant_id,
        email=payload.email,
        password_hash=payload.password_hash,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/tenants/{tenant_id}/users", response_model=list[UserOut])
def list_users(tenant_id: uuid.UUID, db: Session = Depends(get_db)):
    """List all users belonging to *tenant_id*.  **No auth enforced yet** (Task 4.2)."""
    _get_tenant_or_404(tenant_id, db)
    return db.query(User).filter(User.tenant_id == tenant_id).all()
