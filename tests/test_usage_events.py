"""Tests for Task 7.2 — usage events table + Stripe metered billing.

Covers:
1. emit_usage_event creates a row in usage_events.
2. list_usage_events with pagination and filters.
3. Usage event emitted when POST /jobs/optimize is called.
4. Usage event emitted when POST /apply-prices/apply is called.
5. report_usage_to_stripe returns False when billing disabled.
6. report_usage_to_stripe returns False when no subscription.
7. report_usage_to_stripe calls Stripe API when billing enabled (mocked).
8. Tenant isolation (one tenant can't see another's events).
9. Migration file exists and has correct revision chain.

All tests use an in-memory SQLite database (no Postgres/Redis/Stripe required).
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    Role,
    Tenant,
    User,
    UsageEvent,
)
from backend.repositories import usage_repo
from backend.stripe_billing import report_usage_to_stripe

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()
_BATCH_DIR = Path("data/apply_batches")

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
    role: Role = Role.operator,
    email: str = "user@usage.com",
    tenant_name: str = "UsageTenant",
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
    auth_required: bool = True,
    redis_url: str | None = None,
):
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv(
        "SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false"
    )
    if redis_url:
        monkeypatch.setenv("REDIS_URL", redis_url)
    else:
        monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("BILLING_ENABLED", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
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
    get_settings.cache_clear()


@pytest.fixture()
def client_auth_on(db_session, monkeypatch):
    """Client with auth ON and Redis URL configured."""
    yield from _make_client(
        db_session, monkeypatch, auth_required=True, redis_url="redis://localhost:6379/0"
    )


# ===========================================================================
# 1. emit_usage_event creates a row
# ===========================================================================


class TestEmitUsageEvent:
    def test_creates_row(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        evt = usage_repo.emit_usage_event(
            db_session, tenant_id=t.id, event_type="job.optimize", meta={"job_id": "abc"}
        )
        assert evt.id is not None
        assert evt.tenant_id == t.id
        assert evt.event_type == "job.optimize"
        assert evt.meta_json is not None
        data = json.loads(evt.meta_json)
        assert data["job_id"] == "abc"

    def test_creates_row_without_meta(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        evt = usage_repo.emit_usage_event(
            db_session, tenant_id=t.id, event_type="batch.apply"
        )
        assert evt.meta_json is None


# ===========================================================================
# 2. list_usage_events with pagination and filters
# ===========================================================================


class TestListUsageEvents:
    def test_basic_list(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        for i in range(5):
            usage_repo.emit_usage_event(
                db_session, tenant_id=t.id, event_type="job.optimize", meta={"i": i}
            )
        total, items = usage_repo.list_usage_events(db_session, tenant_id=t.id)
        assert total == 5
        assert len(items) == 5

    def test_pagination(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        for i in range(10):
            usage_repo.emit_usage_event(
                db_session, tenant_id=t.id, event_type="job.optimize"
            )
        total, items = usage_repo.list_usage_events(
            db_session, tenant_id=t.id, limit=3, offset=0
        )
        assert total == 10
        assert len(items) == 3

        total2, items2 = usage_repo.list_usage_events(
            db_session, tenant_id=t.id, limit=3, offset=3
        )
        assert total2 == 10
        assert len(items2) == 3

    def test_filter_by_event_type(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        usage_repo.emit_usage_event(db_session, tenant_id=t.id, event_type="job.optimize")
        usage_repo.emit_usage_event(db_session, tenant_id=t.id, event_type="batch.apply")
        usage_repo.emit_usage_event(db_session, tenant_id=t.id, event_type="job.optimize")

        total, items = usage_repo.list_usage_events(
            db_session, tenant_id=t.id, event_type="job.optimize"
        )
        assert total == 2
        assert all(e.event_type == "job.optimize" for e in items)

    def test_filter_by_since(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        old_evt = usage_repo.emit_usage_event(
            db_session, tenant_id=t.id, event_type="job.optimize"
        )
        # manually backdate
        old_evt.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        db_session.commit()

        usage_repo.emit_usage_event(db_session, tenant_id=t.id, event_type="job.optimize")

        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        total, items = usage_repo.list_usage_events(
            db_session, tenant_id=t.id, since=since
        )
        assert total == 1

    def test_filter_by_until(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        usage_repo.emit_usage_event(db_session, tenant_id=t.id, event_type="job.optimize")

        until = datetime(2020, 1, 1, tzinfo=timezone.utc)
        total, items = usage_repo.list_usage_events(
            db_session, tenant_id=t.id, until=until
        )
        assert total == 0


# ===========================================================================
# 3. Usage event emitted from POST /jobs/optimize
# ===========================================================================


class TestUsageEventFromJobsEndpoint:
    @pytest.mark.anyio
    async def test_optimize_emits_usage_event(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)

        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.aclose = AsyncMock()

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock()
        mock_pool.aclose = AsyncMock()

        with (
            patch("backend.jobs_api._get_redis", return_value=mock_redis),
            patch("backend.jobs_api._get_arq_pool", return_value=mock_pool),
        ):
            resp = client_auth_on.post(
                "/jobs/optimize",
                json={
                    "api_username": "test@example.com",
                    "api_password": "pw",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200

        events = db_session.query(UsageEvent).filter(
            UsageEvent.tenant_id == t.id,
            UsageEvent.event_type == "job.optimize",
        ).all()
        assert len(events) == 1
        meta = json.loads(events[0].meta_json)
        assert "job_id" in meta


# ===========================================================================
# 4. Usage event emitted from POST /apply-prices/apply
# ===========================================================================


class TestUsageEventFromApplyEndpoint:
    @pytest.fixture(autouse=True)
    def _enable_apply(self, monkeypatch):
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", "true")
        monkeypatch.setenv("ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED", "true")
        get_settings.cache_clear()

    @pytest.fixture(autouse=True)
    def _clean_dirs(self):
        yield
        if _BATCH_DIR.exists():
            shutil.rmtree(_BATCH_DIR)
        get_settings.cache_clear()

    def test_apply_emits_usage_event(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)

        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget Pro",
                "buy_price": 100.0,
                "old_price": 200.0,
                "new_price": 249.0,
                "change_pct": 24.5,
            },
        ]
        manifest = {
            "batch_id": bid,
            "created_at": "2024-01-01T00:00:00+00:00",
            "optimize_payload": {
                "api_username": "test@example.com",
                "api_password": "secret",
            },
            "product_numbers": ["SKU-001"],
            "changes": changes,
            "summary": {"total": 1, "increases": 1, "decreases": 0, "unchanged": 0},
        }
        _BATCH_DIR.mkdir(parents=True, exist_ok=True)
        (_BATCH_DIR / f"{bid}.json").write_text(json.dumps(manifest), encoding="utf-8")

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 1,
            "failed": 0,
            "errors": [],
        }

        with patch("backend.apply_real_api.DanDomainClient", return_value=mock_instance):
            resp = client_auth_on.post(
                "/apply-prices/apply",
                json={
                    "batch_id": bid,
                    "confirm": True,
                    "api_username": "test@example.com",
                    "api_password": "secret",
                    "site_id": 1,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200

        events = db_session.query(UsageEvent).filter(
            UsageEvent.tenant_id == t.id,
            UsageEvent.event_type == "batch.apply",
        ).all()
        assert len(events) == 1
        meta = json.loads(events[0].meta_json)
        assert "batch_id" in meta
        assert "applied_count" in meta


# ===========================================================================
# 5. report_usage_to_stripe returns False when billing disabled
# ===========================================================================


class TestReportUsageToStripe:
    def test_returns_false_billing_disabled(self, db_session, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        monkeypatch.setenv("BILLING_ENABLED", "false")
        get_settings.cache_clear()
        settings = get_settings()

        t, _u = _seed_tenant_and_user(
            db_session,
            stripe_subscription_id="sub_test_123",
            billing_status="active",
        )
        result = report_usage_to_stripe(t, "job.optimize", settings=settings)
        assert result is False
        get_settings.cache_clear()

    def test_returns_false_no_subscription(self, db_session, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        settings = get_settings()

        t, _u = _seed_tenant_and_user(db_session, billing_status="active")
        result = report_usage_to_stripe(t, "job.optimize", settings=settings)
        assert result is False
        get_settings.cache_clear()

    def test_returns_false_billing_status_not_active(self, db_session, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        settings = get_settings()

        t, _u = _seed_tenant_and_user(
            db_session,
            stripe_subscription_id="sub_test_123",
            billing_status="past_due",
        )
        result = report_usage_to_stripe(t, "job.optimize", settings=settings)
        assert result is False
        get_settings.cache_clear()

    @patch("backend.stripe_billing.stripe.billing.MeterEvent.create")
    def test_calls_stripe_api_when_billing_enabled(
        self, mock_meter_create, db_session, monkeypatch
    ):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        settings = get_settings()

        t, _u = _seed_tenant_and_user(
            db_session,
            stripe_customer_id="cus_test_456",
            stripe_subscription_id="sub_test_123",
            billing_status="active",
        )

        mock_meter_create.return_value = {"identifier": "evt_123"}

        result = report_usage_to_stripe(t, "job.optimize", settings=settings)
        assert result is True

        mock_meter_create.assert_called_once_with(
            event_name="job.optimize",
            payload={
                "stripe_customer_id": "cus_test_456",
                "value": "1",
            },
        )
        get_settings.cache_clear()

    @patch("backend.stripe_billing.stripe.billing.MeterEvent.create")
    def test_returns_false_on_stripe_error(
        self, mock_meter_create, db_session, monkeypatch
    ):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        monkeypatch.setenv("BILLING_ENABLED", "true")
        get_settings.cache_clear()
        settings = get_settings()

        t, _u = _seed_tenant_and_user(
            db_session,
            stripe_subscription_id="sub_test_123",
            billing_status="active",
        )
        mock_meter_create.side_effect = Exception("Stripe API error")

        result = report_usage_to_stripe(t, "job.optimize", settings=settings)
        assert result is False
        get_settings.cache_clear()


# ===========================================================================
# 8. Tenant isolation
# ===========================================================================


class TestTenantIsolation:
    def test_one_tenant_cannot_see_anothers_events(self, db_session):
        t1, _u1 = _seed_tenant_and_user(
            db_session, email="u1@t1.com", tenant_name="T1"
        )
        t2, _u2 = _seed_tenant_and_user(
            db_session, email="u2@t2.com", tenant_name="T2"
        )
        usage_repo.emit_usage_event(
            db_session, tenant_id=t1.id, event_type="job.optimize"
        )
        usage_repo.emit_usage_event(
            db_session, tenant_id=t2.id, event_type="batch.apply"
        )

        total1, items1 = usage_repo.list_usage_events(db_session, tenant_id=t1.id)
        assert total1 == 1
        assert items1[0].event_type == "job.optimize"

        total2, items2 = usage_repo.list_usage_events(db_session, tenant_id=t2.id)
        assert total2 == 1
        assert items2[0].event_type == "batch.apply"


# ===========================================================================
# 9. Migration file exists and has correct revision chain
# ===========================================================================


class TestMigrationFile:
    def test_migration_exists(self):
        path = Path("alembic/versions/0006_add_usage_events.py")
        assert path.exists(), f"Migration file not found at {path}"

    def test_revision_chain(self):
        path = Path("alembic/versions/0006_add_usage_events.py")
        content = path.read_text()
        assert 'revision: str = "0006_usage_events"' in content
        assert 'down_revision' in content
        assert '"0005_stripe_fields"' in content
