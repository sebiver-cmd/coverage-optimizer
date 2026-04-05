"""Credential vault CRUD API (Task 5.1).

Provides endpoints for managing encrypted HostedShop SOAP credentials
scoped to the authenticated user's tenant.

Security invariants
-------------------
- Decrypted secrets are **never** returned in API responses.
- Ciphertext is **never** logged.
- All endpoints require ``admin`` role when auth is enabled.
- When auth is disabled the vault endpoints return 503 to avoid
  confusion (vault requires authentication context).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.crypto import encrypt_str
from backend.db import get_db
from backend.models import HostedShopCredential, User
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CredentialCreate(BaseModel):
    """Payload for creating a new credential entry."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable label (e.g. 'Main shop').")
    site_id: str = Field(..., min_length=1, description="Site / shop ID.")
    api_username: str = Field(..., min_length=1, description="DanDomain API username.")
    api_password: str = Field(..., min_length=1, description="DanDomain API password.")


class CredentialOut(BaseModel):
    """Credential metadata returned in API responses (no secrets)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    site_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_vault_available(settings: Settings) -> None:
    """Raise 503 when auth is off, 500 when encryption key is missing."""
    if not settings.sboptima_auth_required:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth disabled; credential vault is not available.",
        )
    if not settings.encryption_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ENCRYPTION_KEY is not configured; vault operations cannot proceed.",
        )


def _get_current_user_from_request(request: Request) -> User:
    """Extract the authenticated user stashed by the RBAC dependency.

    The ``require_role`` dependency resolves the user and stores it in
    ``request.state.user``.  This helper retrieves it for use by
    credential endpoints that need the caller's ``tenant_id``.
    """
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for vault operations.",
        )
    return user


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/credentials",
    tags=["credentials"],
    dependencies=[Depends(require_role("admin"))],
)


@router.post("/", response_model=CredentialOut, status_code=status.HTTP_201_CREATED)
def create_credential(
    payload: CredentialCreate,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> CredentialOut:
    """Create a new encrypted credential entry for the caller's tenant.

    The ``api_username`` and ``api_password`` fields are encrypted at rest
    using the configured ``ENCRYPTION_KEY``.  The response contains only
    metadata — secrets are never returned.
    """
    _require_vault_available(settings)
    user = _get_current_user_from_request(request)

    # Check for duplicate name within tenant
    existing = (
        db.query(HostedShopCredential)
        .filter(
            HostedShopCredential.tenant_id == user.tenant_id,
            HostedShopCredential.name == payload.name,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A credential named '{payload.name}' already exists for this tenant.",
        )

    try:
        username_enc = encrypt_str(payload.api_username)
        password_enc = encrypt_str(payload.api_password)
    except ValueError as exc:
        logger.error("Encryption failure during credential creation")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    now = datetime.now(timezone.utc)
    cred = HostedShopCredential(
        tenant_id=user.tenant_id,
        name=payload.name,
        site_id=str(payload.site_id),
        api_username_enc=username_enc,
        api_password_enc=password_enc,
        created_at=now,
        updated_at=now,
    )
    db.add(cred)
    db.commit()
    db.refresh(cred)
    return cred


@router.get("/", response_model=list[CredentialOut])
def list_credentials(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[CredentialOut]:
    """List credential metadata for the caller's tenant (no secrets)."""
    _require_vault_available(settings)
    user = _get_current_user_from_request(request)

    return (
        db.query(HostedShopCredential)
        .filter(HostedShopCredential.tenant_id == user.tenant_id)
        .order_by(HostedShopCredential.created_at.desc())
        .all()
    )


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    """Delete a credential entry belonging to the caller's tenant."""
    _require_vault_available(settings)
    user = _get_current_user_from_request(request)

    cred = (
        db.query(HostedShopCredential)
        .filter(
            HostedShopCredential.id == credential_id,
            HostedShopCredential.tenant_id == user.tenant_id,
        )
        .first()
    )
    if cred is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Credential not found.",
        )

    db.delete(cred)
    db.commit()
