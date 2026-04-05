"""Credential resolver for HostedShop SOAP calls (Task 5.1).

Resolves HostedShop credentials from either the vault (when auth is
enabled and ``credential_id`` is provided) or from request-supplied
fields (legacy / auth-off mode).

Security invariants
-------------------
- Plaintext credentials exist only in the returned tuple; they are
  **never** logged.
- When auth is enabled and ``allow_request_credentials_when_authed``
  is ``False`` (default), raw credentials in the request body are
  rejected with HTTP 400.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.crypto import decrypt_str
from backend.models import HostedShopCredential, User

logger = logging.getLogger(__name__)


def resolve_hostedshop_credentials(
    *,
    request_payload: Any,
    current_user: User | None,
    db: Session | None,
    settings: Settings,
) -> tuple[str, str, str]:
    """Resolve HostedShop credentials to ``(site_id, username, password)``.

    Parameters
    ----------
    request_payload:
        The Pydantic request model.  Expected to have optional fields:
        ``credential_id``, ``api_username``, ``api_password``, ``site_id``.
    current_user:
        The authenticated user (``None`` when auth is off).
    db:
        SQLAlchemy session (``None`` when auth is off and no vault lookup
        is needed).
    settings:
        Application settings.

    Returns
    -------
    ``(site_id, username, password)`` — all as strings.

    Raises
    ------
    HTTPException
        On missing / forbidden credential combinations.
    """
    credential_id: uuid.UUID | None = getattr(request_payload, "credential_id", None)
    has_raw_creds = bool(
        getattr(request_payload, "api_username", None)
        and getattr(request_payload, "api_password", None)
    )

    # ------------------------------------------------------------------
    # Auth OFF → legacy mode: use request-supplied credentials
    # ------------------------------------------------------------------
    if not settings.sboptima_auth_required:
        if not has_raw_creds:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_username and api_password are required.",
            )
        return (
            str(getattr(request_payload, "site_id", "1")),
            request_payload.api_username,
            request_payload.api_password,
        )

    # ------------------------------------------------------------------
    # Auth ON
    # ------------------------------------------------------------------
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )

    # Case 1: credential_id supplied → use vault
    if credential_id is not None:
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database session not available for vault lookup.",
            )
        if not settings.encryption_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ENCRYPTION_KEY is not configured; cannot decrypt vault credentials.",
            )

        cred = (
            db.query(HostedShopCredential)
            .filter(
                HostedShopCredential.id == credential_id,
                HostedShopCredential.tenant_id == current_user.tenant_id,
            )
            .first()
        )
        if cred is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Credential not found.",
            )

        try:
            username = decrypt_str(cred.api_username_enc)
            password = decrypt_str(cred.api_password_enc)
        except Exception:
            logger.error("Failed to decrypt credential id=%s", credential_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt stored credentials.",
            )

        return (cred.site_id, username, password)

    # Case 2: raw credentials supplied
    if has_raw_creds:
        if not settings.allow_request_credentials_when_authed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Request-supplied credentials are not allowed when "
                    "authentication is enabled. Use credential_id from the "
                    "vault instead, or set ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED=true."
                ),
            )
        return (
            str(getattr(request_payload, "site_id", "1")),
            request_payload.api_username,
            request_payload.api_password,
        )

    # Case 3: neither credential_id nor raw creds
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Either credential_id or api_username/api_password must be provided.",
    )
