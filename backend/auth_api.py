"""Auth endpoints for SB-Optima (Task 4.2).

Endpoints
---------
- ``POST /auth/signup``   — create a new tenant + first user, return JWT.
- ``POST /auth/login``    — authenticate with email/password, return JWT.
- ``POST /auth/refresh``  — exchange a valid (non-expired) JWT for a fresh one.
- ``GET  /auth/me``       — return the current user (protected test route).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from backend.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from backend.config import Settings, get_settings
from backend.db import get_db
from backend.models import Role, Tenant, User
from backend.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


_BCRYPT_MAX_BYTES = 72


def _validate_password_bytes(v: str) -> str:
    """Reject passwords whose UTF-8 encoding exceeds bcrypt's 72-byte limit."""
    if len(v.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {_BCRYPT_MAX_BYTES} bytes")
    return v


class SignupRequest(BaseModel):
    """Payload for creating a new tenant + first (owner) user."""

    tenant_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def password_max_bytes(cls, v: str) -> str:
        return _validate_password_bytes(v)


class LoginRequest(BaseModel):
    """Payload for authenticating an existing user."""

    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class UserMeResponse(BaseModel):
    """Current-user response for ``GET /auth/me``."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_email(email: str) -> str:
    """Lower-case the email for consistent uniqueness checks."""
    return email.strip().lower()


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Create a new tenant and its first user (role=owner).

    Returns a JWT so the caller is immediately authenticated.
    """
    email = _normalize_email(payload.email)
    logger.debug(
        "Signup attempt: email_len=%d pwd_chars=%d pwd_bytes=%d",
        len(email),
        len(payload.password),
        len(payload.password.encode("utf-8")),
    )

    # Create tenant
    tenant = Tenant(name=payload.tenant_name)
    db.add(tenant)
    db.flush()  # get tenant.id before creating user

    # Check duplicate email within the new tenant (shouldn't happen on
    # signup but be safe).
    existing = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.email == email)
        .first()
    )
    if existing is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    try:
        password_hash = hash_password(payload.password)
    except ValueError:
        # Second line of defence: the Pydantic field_validator already blocks
        # passwords longer than 72 bytes, but if passlib/bcrypt ever raises
        # ValueError for any other reason (e.g. a future library version with
        # stricter checks) we still want a 422 rather than a 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at most 72 bytes",
        )

    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=password_hash,
        role=Role.owner,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(
        sub=user.id,
        tenant_id=tenant.id,
        role=user.role.value,
        settings=settings,
    )
    logger.info("Signup: tenant=%s user=%s", tenant.id, user.id)
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Authenticate a user by email + password and return a JWT.

    Email look-up is case-insensitive.
    """
    email = _normalize_email(payload.email)

    user = db.query(User).filter(User.email == email).first()
    try:
        password_ok = user is not None and verify_password(payload.password, user.password_hash)
    except ValueError:
        password_ok = False
    if not password_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(
        sub=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value,
        settings=settings,
    )
    logger.info("Login: user=%s tenant=%s", user.id, user.tenant_id)
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
):
    """Exchange a valid JWT for a fresh one with a new expiry.

    The caller must present a valid (non-expired) token.  A new token
    is issued with a full ``jwt_access_token_exp_minutes`` lifetime.
    """
    token = create_access_token(
        sub=current_user.id,
        tenant_id=current_user.tenant_id,
        role=current_user.role.value,
        settings=settings,
    )
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# GET /auth/me  (test-protected route)
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserMeResponse, dependencies=[Depends(require_role("viewer"))])
def me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user.

    This endpoint always requires a valid token (regardless of
    ``SBOPTIMA_AUTH_REQUIRED``) and serves as the canonical
    "is my token working?" probe.
    """
    return current_user
