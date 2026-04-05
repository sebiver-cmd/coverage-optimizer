"""Billing gate dependency for SB-Optima (Task 7.1 completion).

Provides :func:`check_billing_gate`, a reusable FastAPI dependency that
enforces an **active** billing subscription on paid endpoints.

Behaviour
---------
- ``billing_enabled=false``          → gate disabled, all requests pass.
- Stripe not configured              → gate disabled (billing not operational).
- ``sboptima_auth_required=false``   → gate disabled (no tenant context).
- No ``tenant_id`` on request state  → gate skipped (anonymous / legacy).
- ``billing_status == "active"``     → pass through.
- Any other billing_status (including NULL / canceled / past_due / inactive)
  → **HTTP 402 Payment Required**.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from backend.config import get_settings

logger = logging.getLogger(__name__)

_DETAIL = "Payment required \u2014 please update your subscription."


async def check_billing_gate(request: Request) -> None:
    """FastAPI dependency that blocks requests from tenants without active billing.

    Add to paid endpoints via ``dependencies=[Depends(check_billing_gate)]``.
    """
    settings = get_settings()

    # Gate disabled when billing is off, Stripe is not configured,
    # or auth is not enforced.
    if not settings.billing_enabled:
        return
    if not settings.stripe_secret_key:
        return
    if not settings.sboptima_auth_required:
        return

    # No tenant context → skip (e.g. unauthenticated legacy call).
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        return

    # Look up tenant billing_status from the database.
    from backend.db import get_db
    from backend.models import Tenant

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None or tenant.billing_status != "active":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=_DETAIL,
            )
    finally:
        # Close the generator.
        try:
            next(db_gen, None)
        except StopIteration:
            pass
