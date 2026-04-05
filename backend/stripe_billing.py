"""Stripe billing helpers for SB-Optima (Task 8.1).

Server-side only — the Stripe secret key **must never** be sent to a UI.

Provides:
- :func:`is_billing_configured` — check whether Stripe keys are present.
- :func:`create_or_get_customer` — idempotently create a Stripe customer.
- :func:`create_checkout_session` — start a Stripe Checkout subscription flow.
- :func:`parse_and_verify_webhook` — verify a webhook signature and return the event.
- :func:`price_id_to_plan` — map a Stripe price ID to an internal plan name.
- :func:`handle_webhook_event` — process a verified Stripe event and update DB.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import stripe
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.models import AuditEvent, Tenant

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def is_billing_configured(settings: Settings) -> bool:
    """Return ``True`` when billing is enabled and Stripe keys are present."""
    return bool(
        settings.billing_enabled
        and settings.stripe_secret_key
    )


def _configure_stripe(settings: Settings) -> None:
    """Set the Stripe API key from settings (call before every SDK call)."""
    stripe.api_key = settings.stripe_secret_key


# ---------------------------------------------------------------------------
# Price → plan mapping
# ---------------------------------------------------------------------------


def price_id_to_plan(price_id: str, settings: Settings) -> str | None:
    """Map a Stripe *price_id* to an internal plan name.

    Returns ``None`` when the price ID is not recognised — the caller
    should fall back to ``"free"`` in that case.
    """
    mapping: dict[str | None, str] = {}
    if settings.stripe_price_id_pro:
        mapping[settings.stripe_price_id_pro] = "pro"
    if settings.stripe_price_id_enterprise:
        mapping[settings.stripe_price_id_enterprise] = "enterprise"
    return mapping.get(price_id)


# ---------------------------------------------------------------------------
# Customer helpers
# ---------------------------------------------------------------------------


def create_or_get_customer(
    tenant: Tenant,
    email: str,
    settings: Settings,
) -> str:
    """Return an existing Stripe customer ID or create a new one.

    If *tenant.stripe_customer_id* is already set the value is returned
    without hitting the Stripe API.
    """
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id

    _configure_stripe(settings)
    customer = stripe.Customer.create(
        email=email,
        metadata={"tenant_id": str(tenant.id), "tenant_name": tenant.name},
    )
    return customer["id"]


# ---------------------------------------------------------------------------
# Checkout session
# ---------------------------------------------------------------------------


def create_checkout_session(
    *,
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    settings: Settings,
    tenant_id: str,
) -> str:
    """Create a Stripe Checkout session and return the checkout URL."""
    _configure_stripe(settings)
    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"tenant_id": str(tenant_id)},
    )
    return session["url"]


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------


def parse_and_verify_webhook(
    payload: bytes,
    sig_header: str,
    webhook_secret: str,
) -> stripe.Event:
    """Verify a Stripe webhook signature and return the parsed event.

    Raises ``stripe.error.SignatureVerificationError`` on failure.
    """
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)


# ---------------------------------------------------------------------------
# Webhook event processing
# ---------------------------------------------------------------------------


def handle_webhook_event(
    event: stripe.Event,
    *,
    db: Session,
    settings: Settings,
) -> dict[str, Any]:
    """Process a verified Stripe webhook *event* and update the DB.

    Returns a summary dict for logging / response.
    """
    event_type: str = event["type"]
    data_object: dict = event["data"]["object"]

    if event_type == "checkout.session.completed":
        return _handle_checkout_completed(data_object, db=db, settings=settings)
    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        return _handle_subscription_change(
            event_type, data_object, db=db, settings=settings
        )

    logger.info("Unhandled Stripe event type: %s", event_type)
    return {"handled": False, "event_type": event_type}


# ---------------------------------------------------------------------------
# Internal handlers
# ---------------------------------------------------------------------------


def _resolve_tenant_by_customer(
    customer_id: str, db: Session
) -> Tenant | None:
    """Look up a tenant by Stripe customer ID."""
    return (
        db.query(Tenant)
        .filter(Tenant.stripe_customer_id == customer_id)
        .first()
    )


def _handle_checkout_completed(
    data: dict, *, db: Session, settings: Settings
) -> dict[str, Any]:
    """Handle ``checkout.session.completed``."""
    customer_id = data.get("customer")
    tenant_id_meta = (data.get("metadata") or {}).get("tenant_id")
    subscription_id = data.get("subscription")

    tenant: Tenant | None = None
    if customer_id:
        tenant = _resolve_tenant_by_customer(customer_id, db)

    # Fallback: find tenant by metadata tenant_id and set customer id
    if tenant is None and tenant_id_meta:
        tenant = db.query(Tenant).filter(
            Tenant.id == tenant_id_meta
        ).first()
        if tenant and customer_id:
            tenant.stripe_customer_id = customer_id

    if tenant is None:
        logger.warning(
            "checkout.session.completed: no tenant found (customer=%s, meta=%s)",
            customer_id,
            tenant_id_meta,
        )
        return {"handled": False, "reason": "tenant_not_found"}

    if subscription_id and not tenant.stripe_subscription_id:
        tenant.stripe_subscription_id = subscription_id

    db.add(
        AuditEvent(
            tenant_id=tenant.id,
            event_type="billing.checkout.completed",
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps(
                {"customer_id": customer_id, "subscription_id": subscription_id}
            ),
        )
    )
    db.commit()
    return {"handled": True, "event_type": "checkout.session.completed"}


def _handle_subscription_change(
    event_type: str,
    data: dict,
    *,
    db: Session,
    settings: Settings,
) -> dict[str, Any]:
    """Handle ``customer.subscription.created|updated|deleted``."""
    customer_id = data.get("customer")
    subscription_id = data.get("id")
    status = data.get("status")  # active, past_due, canceled, …

    tenant: Tenant | None = None
    if customer_id:
        tenant = _resolve_tenant_by_customer(customer_id, db)

    if tenant is None:
        logger.warning(
            "%s: no tenant found for customer=%s", event_type, customer_id
        )
        return {"handled": False, "reason": "tenant_not_found"}

    old_plan = tenant.plan
    old_billing = tenant.billing_status

    # Determine plan from the first subscription item's price
    items = data.get("items", {}).get("data", [])
    new_plan: str | None = None
    if items:
        price_id = items[0].get("price", {}).get("id")
        if price_id:
            new_plan = price_id_to_plan(price_id, settings)

    if event_type == "customer.subscription.deleted":
        tenant.plan = "free"
        tenant.billing_status = "canceled"
    else:
        if new_plan:
            tenant.plan = new_plan
        tenant.billing_status = status

    tenant.stripe_subscription_id = subscription_id

    db.add(
        AuditEvent(
            tenant_id=tenant.id,
            event_type="tenant.plan.updated",
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps({"old": old_plan, "new": tenant.plan, "source": "stripe"}),
        )
    )
    db.add(
        AuditEvent(
            tenant_id=tenant.id,
            event_type="billing.subscription.updated",
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps(
                {
                    "stripe_event": event_type,
                    "subscription_id": subscription_id,
                    "status": status,
                    "old_billing_status": old_billing,
                }
            ),
        )
    )
    db.commit()
    return {
        "handled": True,
        "event_type": event_type,
        "plan": tenant.plan,
        "billing_status": tenant.billing_status,
    }


# ---------------------------------------------------------------------------
# Metered usage reporting (Task 7.2)
# ---------------------------------------------------------------------------


def report_usage_to_stripe(
    tenant: Tenant,
    event_type: str,
    *,
    settings: Settings,
    quantity: int = 1,
) -> bool:
    """Report a usage record to Stripe for metered billing.

    Returns True if reported, False if skipped (billing disabled or no subscription).
    Uses the Stripe Billing Meter Events API (stripe >= 15.x).
    """
    if not is_billing_configured(settings):
        return False
    if not tenant.stripe_subscription_id:
        return False
    if tenant.billing_status != "active":
        return False
    if not tenant.stripe_customer_id:
        return False

    _configure_stripe(settings)
    try:
        stripe.billing.MeterEvent.create(
            event_name=event_type,
            payload={
                "stripe_customer_id": tenant.stripe_customer_id or "",
                "value": str(quantity),
            },
        )
        logger.info(
            "Stripe usage reported: tenant=%s event=%s qty=%d",
            tenant.id,
            event_type,
            quantity,
        )
        return True
    except Exception:
        logger.warning("Stripe usage reporting failed (non-fatal)", exc_info=True)
        return False
