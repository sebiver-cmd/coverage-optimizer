"""Tests for Task 8.1 — Stripe integration (customer + subscription + webhooks).

Covers:
1. Billing not configured → 503 responses.
2. POST /billing/checkout (mocked Stripe SDK).
3. POST /billing/webhook — signature verification + subscription events.
4. GET /billing/status — billing info retrieval.
5. RBAC enforcement (viewer cannot start checkout; admin/owner can).
6. Auth-off → billing endpoints return 503.
7. Webhook handler: plan update + audit events.
8. price_id_to_plan mapping.
9. is_billing_configured helper.

All tests use an in-memory SQLite database (no Postgres/Redis/Stripe required).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import create_access_token, hash_password
from backend.config import get_settings
from backend.db import Base, get_db
from backend.main import app
from backend.models import (
    AuditEvent,
    Role,
    Tenant,
    User,
)
from backend.stripe_billing import (
    handle_webhook_event,
    is_billing_configured,
    price_id_to_plan,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()
_STRIPE_SK = "sk_test_fake_key_for_testing"
_STRIPE_WH = "whsec_fake_webhook_secret"
_PRICE_PRO = "price_pro_test_123"
_PRICE_ENT = "price_ent_test_456"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """Yield a SQLAlchemy session backed by an in-memory SQLite database."""
    engine = create_engine(
        _SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _seed_tenant_and_user(
    db: Session,
    *,
    role: Role = Role.admin,
    email: str = "admin@billing.com",
    tenant_name: str = "BillingTenant",
    plan: str | None = None,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    billing_status: str | None = None,
) -> tuple[Tenant, User]:
    """Create and return a (Tenant, User) pair."""
    t = Tenant(
        id=uuid.uuid4(),
        name=tenant_name,
        plan=plan,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        billing_status=billing_status,
    )
    db.add(t)
    db.flush()
    u = User(
        id=uuid.uuid4(),
        tenant_id=t.id,
        email=email,
        password_hash=hash_password(_PASSWORD),
        role=role,
    )
    db.add(u)
    db.commit()
    db.refresh(t)
    db.refresh(u)
    return t, u


def _make_token(user: User) -> str:
    return create_access_token(
        sub=user.id, tenant_id=user.tenant_id, role=user.role.value
    )


def _make_client(
    db_session: Session,
    monkeypatch,
    *,
    auth_required: bool,
    billing_enabled: bool = True,
    stripe_key: str | None = _STRIPE_SK,
    stripe_wh: str | None = _STRIPE_WH,
    stripe_price_pro: str | None = _PRICE_PRO,
    stripe_price_ent: str | None = _PRICE_ENT,
):
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv(
        "SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false"
    )
    monkeypatch.setenv("BILLING_ENABLED", "true" if billing_enabled else "false")
    if stripe_key:
        monkeypatch.setenv("STRIPE_SECRET_KEY", stripe_key)
    else:
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    if stripe_wh:
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", stripe_wh)
    else:
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    if stripe_price_pro:
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", stripe_price_pro)
    else:
        monkeypatch.delenv("STRIPE_PRICE_ID_PRO", raising=False)
    if stripe_price_ent:
        monkeypatch.setenv("STRIPE_PRICE_ID_ENTERPRISE", stripe_price_ent)
    else:
        monkeypatch.delenv("STRIPE_PRICE_ID_ENTERPRISE", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def client_billing_on(db_session, monkeypatch):
    """Client with auth ON, billing ON, Stripe keys configured."""
    yield from _make_client(db_session, monkeypatch, auth_required=True)


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    """Client with auth OFF → billing should return 503."""
    yield from _make_client(db_session, monkeypatch, auth_required=False)


@pytest.fixture()
def client_no_stripe(db_session, monkeypatch):
    """Client with auth ON but no Stripe keys → billing 503."""
    yield from _make_client(
        db_session,
        monkeypatch,
        auth_required=True,
        stripe_key=None,
        stripe_wh=None,
    )


@pytest.fixture()
def client_billing_disabled(db_session, monkeypatch):
    """Client with auth ON, Stripe keys, but BILLING_ENABLED=false."""
    yield from _make_client(
        db_session, monkeypatch, auth_required=True, billing_enabled=False
    )


# ===========================================================================
# 1. is_billing_configured helper
# ===========================================================================


class TestIsBillingConfigured:
    def test_true_when_key_and_enabled(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", _STRIPE_SK)
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        s = get_settings()
        assert is_billing_configured(s) is True
        get_settings.cache_clear()

    def test_false_when_no_key(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        s = get_settings()
        assert is_billing_configured(s) is False
        get_settings.cache_clear()

    def test_false_when_disabled(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", _STRIPE_SK)
        monkeypatch.setenv("BILLING_ENABLED", "false")
        get_settings.cache_clear()
        s = get_settings()
        assert is_billing_configured(s) is False
        get_settings.cache_clear()


# ===========================================================================
# 2. price_id_to_plan mapping
# ===========================================================================


class TestPriceIdToPlan:
    def test_maps_pro(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", _PRICE_PRO)
        monkeypatch.setenv("STRIPE_PRICE_ID_ENTERPRISE", _PRICE_ENT)
        get_settings.cache_clear()
        s = get_settings()
        assert price_id_to_plan(_PRICE_PRO, s) == "pro"
        get_settings.cache_clear()

    def test_maps_enterprise(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", _PRICE_PRO)
        monkeypatch.setenv("STRIPE_PRICE_ID_ENTERPRISE", _PRICE_ENT)
        get_settings.cache_clear()
        s = get_settings()
        assert price_id_to_plan(_PRICE_ENT, s) == "enterprise"
        get_settings.cache_clear()

    def test_unknown_returns_none(self, monkeypatch):
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", _PRICE_PRO)
        get_settings.cache_clear()
        s = get_settings()
        assert price_id_to_plan("price_unknown", s) is None
        get_settings.cache_clear()


# ===========================================================================
# 3. Auth-off → billing endpoints return 503
# ===========================================================================


class TestAuthOffReturns503:
    def test_checkout_503(self, client_auth_off, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_auth_off.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503

    def test_status_503(self, client_auth_off, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_auth_off.get(
            "/billing/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503


# ===========================================================================
# 4. Billing not configured (no Stripe keys) → 503
# ===========================================================================


class TestNoStripeReturns503:
    def test_checkout_503(self, client_no_stripe, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_no_stripe.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503

    def test_status_503(self, client_no_stripe, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_no_stripe.get(
            "/billing/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503


# ===========================================================================
# 5. Billing disabled → 503
# ===========================================================================


class TestBillingDisabled503:
    def test_checkout_503(self, client_billing_disabled, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_billing_disabled.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503

    def test_status_503(self, client_billing_disabled, db_session):
        t, u = _seed_tenant_and_user(db_session)
        token = _make_token(u)
        resp = client_billing_disabled.get(
            "/billing/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 503


# ===========================================================================
# 6. POST /billing/checkout
# ===========================================================================


class TestCheckout:
    @patch("backend.stripe_billing.stripe.checkout.Session.create")
    @patch("backend.stripe_billing.stripe.Customer.create")
    def test_checkout_creates_customer_and_session(
        self, mock_cust, mock_session, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)

        mock_cust.return_value = {"id": "cus_test_123"}
        mock_session.return_value = {"url": "https://checkout.stripe.com/pay/cs_test"}

        resp = client_billing_on.post(
            "/billing/checkout",
            json={
                "plan": "pro",
                "success_url": "http://ok",
                "cancel_url": "http://no",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["checkout_url"] == "https://checkout.stripe.com/pay/cs_test"

        mock_cust.assert_called_once()
        mock_session.assert_called_once()

        # Verify tenant got customer_id persisted
        db_session.refresh(t)
        assert t.stripe_customer_id == "cus_test_123"

    @patch("backend.stripe_billing.stripe.checkout.Session.create")
    def test_checkout_reuses_existing_customer(
        self, mock_session, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(
            db_session, role=Role.admin, stripe_customer_id="cus_existing_42"
        )
        token = _make_token(u)

        mock_session.return_value = {"url": "https://checkout.stripe.com/pay/cs_reuse"}

        resp = client_billing_on.post(
            "/billing/checkout",
            json={
                "plan": "enterprise",
                "success_url": "http://ok",
                "cancel_url": "http://no",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["checkout_url"] == "https://checkout.stripe.com/pay/cs_reuse"

    def test_checkout_invalid_plan(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)

        resp = client_billing_on.post(
            "/billing/checkout",
            json={
                "plan": "mega",
                "success_url": "http://ok",
                "cancel_url": "http://no",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    def test_checkout_free_plan_rejected(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)

        resp = client_billing_on.post(
            "/billing/checkout",
            json={
                "plan": "free",
                "success_url": "http://ok",
                "cancel_url": "http://no",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


# ===========================================================================
# 7. RBAC for checkout
# ===========================================================================


class TestCheckoutRBAC:
    @patch("backend.stripe_billing.stripe.checkout.Session.create")
    @patch("backend.stripe_billing.stripe.Customer.create")
    def test_admin_can_checkout(
        self, mock_cust, mock_session, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        mock_cust.return_value = {"id": "cus_test"}
        mock_session.return_value = {"url": "https://checkout.stripe.com/pay/ok"}

        resp = client_billing_on.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    @patch("backend.stripe_billing.stripe.checkout.Session.create")
    @patch("backend.stripe_billing.stripe.Customer.create")
    def test_owner_can_checkout(
        self, mock_cust, mock_session, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(db_session, role=Role.owner)
        token = _make_token(u)
        mock_cust.return_value = {"id": "cus_test"}
        mock_session.return_value = {"url": "https://checkout.stripe.com/pay/ok"}

        resp = client_billing_on.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_viewer_cannot_checkout(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)

        resp = client_billing_on.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_operator_cannot_checkout(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)

        resp = client_billing_on.post(
            "/billing/checkout",
            json={"plan": "pro", "success_url": "http://ok", "cancel_url": "http://no"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ===========================================================================
# 8. GET /billing/status
# ===========================================================================


class TestBillingStatus:
    def test_status_returns_billing_info(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.viewer,
            plan="pro",
            stripe_customer_id="cus_abc",
            stripe_subscription_id="sub_xyz",
            billing_status="active",
        )
        token = _make_token(u)

        resp = client_billing_on.get(
            "/billing/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "pro"
        assert data["billing_status"] == "active"
        assert data["stripe_customer_id"] == "cus_abc"
        assert data["stripe_subscription_id"] == "sub_xyz"

    def test_status_empty_tenant(self, client_billing_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)

        resp = client_billing_on.get(
            "/billing/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] is None
        assert data["billing_status"] is None


# ===========================================================================
# 9. POST /billing/webhook — signature verification
# ===========================================================================


class TestWebhook:
    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_valid_signature_processes_event(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(
            db_session,
            stripe_customer_id="cus_wh_1",
            plan="free",
        )

        mock_event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_wh_1",
                    "customer": "cus_wh_1",
                    "status": "active",
                    "items": {
                        "data": [
                            {"price": {"id": _PRICE_PRO}}
                        ]
                    },
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{"fake":"payload"}',
            headers={
                "stripe-signature": "t=123,v1=fakesig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        mock_construct.assert_called_once()

        # Verify plan was updated
        db_session.refresh(t)
        assert t.plan == "pro"
        assert t.billing_status == "active"
        assert t.stripe_subscription_id == "sub_wh_1"

    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_invalid_signature_returns_400(
        self, mock_construct, client_billing_on, db_session
    ):
        mock_construct.side_effect = Exception("Signature verification failed")

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{"test":"payload"}',
            headers={
                "stripe-signature": "t=123,v1=bad",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 400
        assert "Invalid signature" in resp.json()["detail"]

    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_checkout_completed_sets_customer_id(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(db_session)

        mock_event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_new_checkout",
                    "subscription": "sub_new_checkout",
                    "metadata": {"tenant_id": str(t.id)},
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

        db_session.refresh(t)
        assert t.stripe_customer_id == "cus_new_checkout"
        assert t.stripe_subscription_id == "sub_new_checkout"

    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_subscription_deleted_sets_free(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(
            db_session,
            stripe_customer_id="cus_del_1",
            plan="pro",
            billing_status="active",
        )

        mock_event = {
            "type": "customer.subscription.deleted",
            "data": {
                "object": {
                    "id": "sub_del_1",
                    "customer": "cus_del_1",
                    "status": "canceled",
                    "items": {"data": []},
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

        db_session.refresh(t)
        assert t.plan == "free"
        assert t.billing_status == "canceled"

    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_subscription_created_maps_enterprise(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(
            db_session,
            stripe_customer_id="cus_ent_1",
            plan="free",
        )

        mock_event = {
            "type": "customer.subscription.created",
            "data": {
                "object": {
                    "id": "sub_ent_1",
                    "customer": "cus_ent_1",
                    "status": "active",
                    "items": {
                        "data": [
                            {"price": {"id": _PRICE_ENT}}
                        ]
                    },
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

        db_session.refresh(t)
        assert t.plan == "enterprise"
        assert t.billing_status == "active"


# ===========================================================================
# 10. Audit events emitted
# ===========================================================================


class TestAuditEvents:
    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_subscription_update_emits_audit_events(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(
            db_session,
            stripe_customer_id="cus_audit_1",
            plan="free",
        )

        mock_event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_audit_1",
                    "customer": "cus_audit_1",
                    "status": "active",
                    "items": {
                        "data": [{"price": {"id": _PRICE_PRO}}]
                    },
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.tenant_id == t.id)
            .all()
        )
        event_types = {e.event_type for e in events}
        assert "tenant.plan.updated" in event_types
        assert "billing.subscription.updated" in event_types

        # Check tenant.plan.updated meta
        plan_evt = next(e for e in events if e.event_type == "tenant.plan.updated")
        meta = json.loads(plan_evt.meta_json)
        assert meta["old"] == "free"
        assert meta["new"] == "pro"
        assert meta["source"] == "stripe"

    @patch("backend.stripe_billing.stripe.Webhook.construct_event")
    def test_checkout_completed_emits_audit(
        self, mock_construct, client_billing_on, db_session
    ):
        t, u = _seed_tenant_and_user(db_session)

        mock_event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_checkout_audit",
                    "subscription": "sub_checkout_audit",
                    "metadata": {"tenant_id": str(t.id)},
                }
            },
        }
        mock_construct.return_value = mock_event

        resp = client_billing_on.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200

        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.tenant_id == t.id)
            .all()
        )
        event_types = {e.event_type for e in events}
        assert "billing.checkout.completed" in event_types


# ===========================================================================
# 11. Webhook with no billing config → 503
# ===========================================================================


class TestWebhookNoBilling:
    def test_webhook_503_no_stripe(self, client_no_stripe):
        resp = client_no_stripe.post(
            "/billing/webhook",
            content=b'{}',
            headers={
                "stripe-signature": "t=123,v1=sig",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 503


# ===========================================================================
# 12. handle_webhook_event unit — unhandled event type
# ===========================================================================


class TestHandleWebhookEventUnit:
    def test_unhandled_event_returns_not_handled(self, db_session, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", _STRIPE_SK)
        monkeypatch.setenv("BILLING_ENABLED", "true")
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", _PRICE_PRO)
        get_settings.cache_clear()
        s = get_settings()

        event = {
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_123"}},
        }
        result = handle_webhook_event(event, db=db_session, settings=s)
        assert result["handled"] is False
        assert result["event_type"] == "payment_intent.succeeded"
        get_settings.cache_clear()

    def test_subscription_update_no_tenant_found(self, db_session, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", _STRIPE_SK)
        monkeypatch.setenv("BILLING_ENABLED", "true")
        monkeypatch.setenv("STRIPE_PRICE_ID_PRO", _PRICE_PRO)
        get_settings.cache_clear()
        s = get_settings()

        event = {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_ghost",
                    "customer": "cus_nonexistent",
                    "status": "active",
                    "items": {"data": []},
                }
            },
        }
        result = handle_webhook_event(event, db=db_session, settings=s)
        assert result["handled"] is False
        assert result["reason"] == "tenant_not_found"
        get_settings.cache_clear()
