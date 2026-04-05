"""Authentication helpers for SB-Optima (Task 4.2).

Provides:
- Password hashing / verification via *passlib* + bcrypt.
- JWT creation / decoding via *python-jose*.
- A FastAPI dependency :func:`get_current_user` that reads an
  ``Authorization: Bearer <token>`` header and resolves the caller to
  a :class:`~backend.models.User` row.
- An optional-auth variant :func:`get_optional_current_user` that
  returns ``None`` instead of raising when ``SBOPTIMA_AUTH_REQUIRED``
  is ``False`` and no token is provided.

Security invariants
-------------------
- Plaintext passwords are **never** stored or logged.
- Raw JWTs are **never** logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.db import get_db
from backend.models import User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` when *plain* matches the *hashed* value."""
    return _pwd_ctx.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(
    *,
    sub: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str,
    settings: Settings | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT containing user identity claims.

    Parameters
    ----------
    sub:
        The user ID (``User.id``).
    tenant_id:
        The tenant ID the user belongs to.
    role:
        The user's role string (e.g. ``"operator"``).
    settings:
        Optional :class:`Settings` instance — resolved via
        :func:`get_settings` when *None*.
    expires_delta:
        Custom token lifetime; defaults to
        ``settings.jwt_access_token_exp_minutes``.
    """
    if settings is None:
        settings = get_settings()

    secret = settings.get_jwt_secret()
    algorithm = settings.jwt_algorithm

    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.jwt_access_token_exp_minutes)
    )

    payload = {
        "sub": str(sub),
        "tenant_id": str(tenant_id),
        "role": role,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(
    token: str,
    *,
    settings: Settings | None = None,
) -> dict:
    """Decode and verify a JWT, returning the payload dict.

    Raises :class:`jose.JWTError` on any validation failure (expired,
    bad signature, malformed, …).
    """
    if settings is None:
        settings = get_settings()

    secret = settings.get_jwt_secret()
    algorithm = settings.jwt_algorithm
    return jwt.decode(token, secret, algorithms=[algorithm])


# ---------------------------------------------------------------------------
# FastAPI security scheme
# ---------------------------------------------------------------------------

# auto_error=False so we can handle missing tokens gracefully depending
# on the SBOPTIMA_AUTH_REQUIRED flag.
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Shared token → User resolution (used by both strict and optional deps)
# ---------------------------------------------------------------------------

_INVALID_TOKEN = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _resolve_user_from_token(
    token: str,
    db: Session,
    settings: Settings,
) -> User:
    """Decode *token*, look up the user, and return the :class:`User`.

    Raises ``401`` on any problem (bad signature, expired, unknown user).
    """
    try:
        payload = decode_token(token, settings=settings)
    except JWTError:
        raise _INVALID_TOKEN

    user_id_str = payload.get("sub")
    if user_id_str is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ---------------------------------------------------------------------------
# get_current_user — strict (always requires valid token)
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the calling user from the JWT in the *Authorization* header.

    On success the resolved :class:`User` is also stashed on
    ``request.state.user`` and ``request.state.tenant_id`` for
    downstream middleware / dependencies.

    Raises ``401`` when the token is missing, invalid, expired, or
    the referenced user no longer exists.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = _resolve_user_from_token(credentials.credentials, db, settings)
    request.state.user = user
    request.state.tenant_id = user.tenant_id
    return user


# ---------------------------------------------------------------------------
# get_optional_current_user — respects SBOPTIMA_AUTH_REQUIRED flag
# ---------------------------------------------------------------------------


async def get_optional_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Optional[User]:
    """Like :func:`get_current_user` but returns ``None`` instead of 401
    when ``sboptima_auth_required`` is ``False`` and no token is present.

    If a token **is** present it is always validated — a bad token is
    still rejected even in non-required mode.
    """
    if credentials is None:
        if settings.sboptima_auth_required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing authentication token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    # Token was provided — validate it strictly.
    user = _resolve_user_from_token(credentials.credentials, db, settings)
    request.state.user = user
    request.state.tenant_id = user.tenant_id
    return user
