"""Role-based access control (RBAC) dependency for SB-Optima (Task 4.3).

Provides:
- ``ROLE_ORDER`` mapping from :class:`~backend.models.Role` to an integer
  privilege level.
- :func:`require_role` factory that returns a FastAPI dependency callable
  enforcing a minimum-role gate on endpoints.

When ``SBOPTIMA_AUTH_REQUIRED=false`` (the default during migration / dev),
the dependency is a no-op — it allows every request through without
checking authentication or role, preserving legacy behaviour.

When ``SBOPTIMA_AUTH_REQUIRED=true``, the dependency:

1. Resolves the caller via :func:`~backend.auth.get_current_user`.
2. Compares the caller's role against the required minimum.
3. Raises HTTP 403 if the caller's role is insufficient.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import Settings, get_settings
from backend.models import Role, User

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role ordering (lowest → highest privilege)
# ---------------------------------------------------------------------------

ROLE_ORDER: dict[Role, int] = {
    Role.viewer: 0,
    Role.operator: 1,
    Role.admin: 2,
    Role.owner: 3,
}


def _role_level(role: Role | str) -> int:
    """Return the integer privilege level for *role*.

    Accepts both :class:`Role` enum members and plain strings.
    """
    if isinstance(role, str):
        role = Role(role)
    return ROLE_ORDER[role]


# ---------------------------------------------------------------------------
# require_role dependency factory
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def require_role(min_role: Role | str):
    """Return a FastAPI dependency callable that enforces a minimum role.

    Usage (router-level)::

        router = APIRouter(dependencies=[Depends(require_role("operator"))])

    Usage (per-endpoint)::

        @router.get("/foo", dependencies=[Depends(require_role("admin"))])
        def foo(): ...

    Behaviour
    ---------
    - ``SBOPTIMA_AUTH_REQUIRED=false`` → no-op (returns ``None``).
    - ``SBOPTIMA_AUTH_REQUIRED=true``  → requires a valid JWT whose role
      is ≥ *min_role*; raises 403 on insufficient privilege, 401 on
      missing/invalid token (delegated to :func:`get_current_user`).
    """
    min_level = _role_level(min_role)

    async def _rbac_dependency(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
        settings: Settings = Depends(get_settings),
    ) -> Optional[User]:
        if not settings.sboptima_auth_required:
            return None

        # Auth is required — resolve user from token.
        # Import lazily to avoid circular imports and so that get_db is
        # only resolved when auth is actually enforced.
        from backend.auth import get_current_user
        from backend.db import get_db

        db_gen = get_db()
        db = next(db_gen)
        try:
            user: User = await get_current_user(
                request=request,
                credentials=credentials,
                db=db,
                settings=settings,
            )
        except Exception:
            # Ensure the generator is properly closed on error
            try:
                next(db_gen, None)
            except StopIteration:
                pass
            raise
        # Close the generator
        try:
            next(db_gen, None)
        except StopIteration:
            pass

        if _role_level(user.role) < min_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _rbac_dependency
