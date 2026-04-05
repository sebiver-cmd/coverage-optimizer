"""Billing API routes for SB-Optima (Task 8.1).

Endpoints:
- ``POST /billing/checkout``  — start a Stripe Checkout session (admin+).
- ``GET  /billing/status``    — current tenant billing info (viewer+).
- ``POST /billing/webhook``   — Stripe webhook receiver (no JWT; signature-verified).

All billing endpoints return **503** when:
- ``BILLING_ENABLED=false``, or
- Stripe keys are missing, or
- ``SBOPTIMA_AUTH_REQUIRED=false`` (except the webhook which is always mounted).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from backend.config import Settings, get_settings
from backend.plans import PLANS
from backend.rbac import require_role
from backend.stripe_billing import (
    create_checkout_session,
    create_or_get_customer,
    handle_webhook_event,
    is_billing_configured,
    parse_and_verify_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Body for ``POST /billing/checkout``."""

    plan: str
    success_url: str
    cancel_url: str


class CheckoutResponse(BaseModel):
    """Response for ``POST /billing/checkout``."""

    checkout_url: str


class BillingStatusResponse(BaseModel):
    """Response for ``GET /billing/status``."""

    plan: Optional[str] = None
    billing_status: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------


def _billing_unavailable(reason: str = "Billing is not available.") -> HTTPException:
    return HTTPException(status_code=503, detail=reason)


def _require_billing(settings: Settings) -> None:
    """Raise 503 if billing is not fully configured."""
    if not settings.sboptima_auth_required:
        raise _billing_unavailable(
            "Billing requires auth — set SBOPTIMA_AUTH_REQUIRED=true."
        )
    if not is_billing_configured(settings):
        raise _billing_unavailable(
            "Billing is not configured — set STRIPE_SECRET_KEY and BILLING_ENABLED=true."
        )


# ---------------------------------------------------------------------------
# POST /billing/checkout
# ---------------------------------------------------------------------------


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    dependencies=[Depends(require_role("admin"))],
)
def billing_checkout(body: CheckoutRequest, request: Request) -> CheckoutResponse:
    """Create a Stripe Checkout session for a subscription plan change."""
    settings = get_settings()
    _require_billing(settings)

    plan_lower = body.plan.lower()
    if plan_lower not in PLANS or plan_lower == "free":
        raise HTTPException(
            status_code=422,
            detail=f"Invalid checkout plan: {body.plan!r}. Use 'pro' or 'enterprise'.",
        )

    # Resolve the Stripe price ID for the requested plan
    price_id: str | None = None
    if plan_lower == "pro":
        price_id = settings.stripe_price_id_pro
    elif plan_lower == "enterprise":
        price_id = settings.stripe_price_id_enterprise

    if not price_id:
        raise _billing_unavailable(
            f"No Stripe price configured for plan {plan_lower!r}."
        )

    # Resolve tenant + user
    tenant_id = getattr(request.state, "tenant_id", None)
    user = getattr(request.state, "user", None)
    if tenant_id is None:
        raise _billing_unavailable("No tenant context.")

    from backend.db import get_db
    from backend.models import Tenant

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise _billing_unavailable("Tenant not found.")

        email = user.email if user else f"tenant-{tenant.id}@sboptima.local"
        customer_id = create_or_get_customer(tenant, email, settings)

        # Persist customer id if newly created
        if not tenant.stripe_customer_id:
            tenant.stripe_customer_id = customer_id
            db.commit()
            db.refresh(tenant)

        url = create_checkout_session(
            customer_id=customer_id,
            price_id=price_id,
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            settings=settings,
            tenant_id=str(tenant.id),
        )
        return CheckoutResponse(checkout_url=url)
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass


# ---------------------------------------------------------------------------
# GET /billing/status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=BillingStatusResponse,
    dependencies=[Depends(require_role("viewer"))],
)
def billing_status(request: Request) -> BillingStatusResponse:
    """Return the current tenant's billing info."""
    settings = get_settings()
    _require_billing(settings)

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _billing_unavailable("No tenant context.")

    from backend.db import get_db
    from backend.models import Tenant

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant is None:
            raise _billing_unavailable("Tenant not found.")

        return BillingStatusResponse(
            plan=tenant.plan,
            billing_status=tenant.billing_status,
            stripe_customer_id=tenant.stripe_customer_id,
            stripe_subscription_id=tenant.stripe_subscription_id,
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass


# ---------------------------------------------------------------------------
# POST /billing/webhook  (no JWT auth; Stripe-signed)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def billing_webhook(request: Request) -> Response:
    """Stripe webhook receiver — verifies signature and processes events.

    This endpoint does **not** require JWT authentication.  Replay
    protection is provided by Stripe's signature timestamp verification.
    """
    settings = get_settings()

    if not is_billing_configured(settings):
        raise _billing_unavailable("Billing is not configured.")

    webhook_secret = settings.stripe_webhook_secret
    if not webhook_secret:
        raise _billing_unavailable("Webhook secret is not configured.")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = parse_and_verify_webhook(payload, sig_header, webhook_secret)
    except Exception as exc:
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid signature.") from exc

    # Process the event
    from backend.db import get_db
    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        result = handle_webhook_event(event, db=db, settings=settings)
        logger.info("Stripe webhook processed: %s", result)
        return Response(status_code=200, content="ok")
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass
