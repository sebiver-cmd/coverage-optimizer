"""Tests for Task 7.1 — Usage limits / quotas (per tenant).

Covers:
1. Quota model: Tenant limit columns exist and default to NULL.
2. Day window calculation: correct UTC boundaries.
3. Usage counting: separate counters for optimize_job vs apply.
4. Quota enforcement: blocks at limit (429) with correct reset timestamp.
5. Tenant isolation: tenant A usage does not affect tenant B.
6. ``GET /usage`` endpoint: returns correct counts and limits.
7. Auth-off mode: quota checks are bypassed and ``/usage`` returns 503.
8. Audit event recorded for quota blocks.
9. Window boundary correctness: seeded ``created_at`` values.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
    ApplyBatch,
    OptimizationJob,
    Role,
    Tenant,
    User,
)
from backend.quotas import (
    QuotaExceeded,
    check_quota,
    get_day_window,
    get_limits,
    get_usage,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()


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
    email: str = "user@t1.com",
    tenant_name: str = "Tenant-1",
    daily_optimize_jobs_limit: int | None = None,
    daily_apply_limit: int | None = None,
    daily_optimize_sync_limit: int | None = None,
) -> tuple[Tenant, User]:
    """Create and return a (Tenant, User) pair with optional quota limits."""
    t = Tenant(
        id=uuid.uuid4(),
        name=tenant_name,
        daily_optimize_jobs_limit=daily_optimize_jobs_limit,
        daily_apply_limit=daily_apply_limit,
        daily_optimize_sync_limit=daily_optimize_sync_limit,
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
def client_auth_on(db_session, monkeypatch):
    yield from _make_client(
        db_session,
        monkeypatch,
        auth_required=True,
        redis_url="redis://localhost:6379/0",
    )


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    yield from _make_client(db_session, monkeypatch, auth_required=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_jobs(db: Session, tenant_id: uuid.UUID, count: int, created_at: datetime):
    """Seed *count* optimization_jobs for tenant at *created_at*."""
    for _ in range(count):
        db.add(
            OptimizationJob(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                status="queued",
                created_at=created_at,
            )
        )
    db.commit()


def _seed_apply_batches(
    db: Session, tenant_id: uuid.UUID, count: int, created_at: datetime
):
    """Seed *count* apply batches for tenant at *created_at*."""
    for _ in range(count):
        db.add(
            ApplyBatch(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                mode="apply",
                status="created",
                created_at=created_at,
            )
        )
    db.commit()


# ===================================================================
# 1. Tenant model: limit columns exist and default to NULL
# ===================================================================


class TestTenantLimitColumns:
    def test_limit_columns_default_null(self, db_session):
        t = Tenant(id=uuid.uuid4(), name="Test")
        db_session.add(t)
        db_session.commit()
        db_session.refresh(t)
        assert t.daily_optimize_jobs_limit is None
        assert t.daily_apply_limit is None
        assert t.daily_optimize_sync_limit is None

    def test_limit_columns_set(self, db_session):
        t = Tenant(
            id=uuid.uuid4(),
            name="Test",
            daily_optimize_jobs_limit=200,
            daily_apply_limit=50,
            daily_optimize_sync_limit=100,
        )
        db_session.add(t)
        db_session.commit()
        db_session.refresh(t)
        assert t.daily_optimize_jobs_limit == 200
        assert t.daily_apply_limit == 50
        assert t.daily_optimize_sync_limit == 100


# ===================================================================
# 2. Day window calculation
# ===================================================================


class TestDayWindow:
    def test_day_window_basic(self):
        now = datetime(2026, 4, 5, 14, 30, 0, tzinfo=timezone.utc)
        start, end = get_day_window(now)
        assert start == datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)

    def test_day_window_midnight(self):
        now = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
        start, end = get_day_window(now)
        assert start == datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)

    def test_day_window_end_of_day(self):
        now = datetime(2026, 4, 5, 23, 59, 59, tzinfo=timezone.utc)
        start, end = get_day_window(now)
        assert start == datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)

    def test_day_window_naive_gets_utc(self):
        """A naive datetime is treated as UTC."""
        now = datetime(2026, 4, 5, 12, 0, 0)
        start, end = get_day_window(now)
        assert start.tzinfo == timezone.utc
        assert end.tzinfo == timezone.utc


# ===================================================================
# 3. Usage counting
# ===================================================================


class TestGetUsage:
    def test_empty_counts(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        start, end = get_day_window(now)
        usage = get_usage(db_session, t.id, start, end)
        assert usage == {"optimize_job": 0, "apply": 0, "optimize_sync": 0}

    def test_counts_optimize_jobs_in_window(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 5, now)
        start, end = get_day_window(now)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 5
        assert usage["apply"] == 0

    def test_counts_apply_batches_in_window(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        _seed_apply_batches(db_session, t.id, 3, now)
        start, end = get_day_window(now)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 0
        assert usage["apply"] == 3

    def test_separate_counters(self, db_session):
        """Optimize and apply counters are independent."""
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 7, now)
        _seed_apply_batches(db_session, t.id, 2, now)
        start, end = get_day_window(now)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 7
        assert usage["apply"] == 2

    def test_excludes_outside_window(self, db_session):
        """Rows from yesterday don't count."""
        t, _u = _seed_tenant_and_user(db_session)
        today = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        yesterday = datetime(2026, 4, 4, 23, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 3, yesterday)
        _seed_jobs(db_session, t.id, 2, today)
        start, end = get_day_window(today)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 2

    def test_only_apply_mode_counted(self, db_session):
        """dry_run batches are NOT counted for apply quota."""
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        # Add a dry_run batch
        db_session.add(
            ApplyBatch(
                id=uuid.uuid4(),
                tenant_id=t.id,
                mode="dry_run",
                status="created",
                created_at=now,
            )
        )
        # Add a real apply batch
        db_session.add(
            ApplyBatch(
                id=uuid.uuid4(),
                tenant_id=t.id,
                mode="apply",
                status="created",
                created_at=now,
            )
        )
        db_session.commit()
        start, end = get_day_window(now)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["apply"] == 1


# ===================================================================
# 4. get_limits
# ===================================================================


class TestGetLimits:
    def test_unlimited_when_null(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        limits = get_limits(t)
        assert limits == {
            "optimize_job": None,
            "apply": None,
            "optimize_sync": None,
        }

    def test_limits_set(self, db_session):
        t, _u = _seed_tenant_and_user(
            db_session,
            daily_optimize_jobs_limit=200,
            daily_apply_limit=50,
        )
        limits = get_limits(t)
        assert limits["optimize_job"] == 200
        assert limits["apply"] == 50
        assert limits["optimize_sync"] is None


# ===================================================================
# 5. Quota enforcement
# ===================================================================


class TestCheckQuota:
    def test_unlimited_never_blocks(self, db_session):
        """NULL limit means unlimited — never raises."""
        t, _u = _seed_tenant_and_user(db_session)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 999, now)
        # Should not raise
        check_quota(db_session, t, "optimize_job", now)

    def test_blocks_at_limit(self, db_session):
        """Exactly at limit → raises 429."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=5
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 5, now)
        with pytest.raises(QuotaExceeded) as exc_info:
            check_quota(db_session, t, "optimize_job", now)
        assert exc_info.value.status_code == 429
        assert exc_info.value.used == 5
        assert exc_info.value.limit == 5
        # Reset should be next day midnight UTC
        assert exc_info.value.reset_at == "2026-04-06T00:00:00+00:00"

    def test_blocks_above_limit(self, db_session):
        """Above limit → raises 429."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_apply_limit=3
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_apply_batches(db_session, t.id, 5, now)
        with pytest.raises(QuotaExceeded) as exc_info:
            check_quota(db_session, t, "apply", now)
        assert exc_info.value.status_code == 429
        assert exc_info.value.used == 5
        assert exc_info.value.limit == 3

    def test_allows_under_limit(self, db_session):
        """Under limit → no exception."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=10
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 9, now)
        check_quota(db_session, t, "optimize_job", now)  # should not raise

    def test_apply_quota_independent(self, db_session):
        """Apply quota is checked against apply batches, not optimize jobs."""
        t, _u = _seed_tenant_and_user(
            db_session,
            daily_optimize_jobs_limit=100,
            daily_apply_limit=2,
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 50, now)  # doesn't affect apply
        _seed_apply_batches(db_session, t.id, 2, now)
        # optimize should still work
        check_quota(db_session, t, "optimize_job", now)
        # apply should be blocked
        with pytest.raises(QuotaExceeded):
            check_quota(db_session, t, "apply", now)

    def test_unknown_action_raises_value_error(self, db_session):
        t, _u = _seed_tenant_and_user(db_session)
        with pytest.raises(ValueError, match="Unknown quota action"):
            check_quota(db_session, t, "unknown_action")

    def test_reset_time_is_correct_iso(self, db_session):
        """The reset_at timestamp is the next UTC midnight in ISO format."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=1
        )
        now = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 1, now)
        with pytest.raises(QuotaExceeded) as exc_info:
            check_quota(db_session, t, "optimize_job", now)
        assert exc_info.value.reset_at == "2027-01-01T00:00:00+00:00"


# ===================================================================
# 6. Tenant isolation
# ===================================================================


class TestTenantIsolation:
    def test_usage_isolated_between_tenants(self, db_session):
        """Tenant A's jobs don't count towards tenant B's quota."""
        ta, _ua = _seed_tenant_and_user(
            db_session,
            email="a@t1.com",
            tenant_name="A",
            daily_optimize_jobs_limit=5,
        )
        tb, _ub = _seed_tenant_and_user(
            db_session,
            email="b@t2.com",
            tenant_name="B",
            daily_optimize_jobs_limit=5,
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, ta.id, 5, now)
        _seed_jobs(db_session, tb.id, 2, now)

        # A should be blocked
        with pytest.raises(QuotaExceeded):
            check_quota(db_session, ta, "optimize_job", now)

        # B should pass
        check_quota(db_session, tb, "optimize_job", now)

    def test_usage_counts_isolated(self, db_session):
        ta, _ua = _seed_tenant_and_user(
            db_session, email="a@t1.com", tenant_name="A"
        )
        tb, _ub = _seed_tenant_and_user(
            db_session, email="b@t2.com", tenant_name="B"
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, ta.id, 10, now)
        _seed_apply_batches(db_session, tb.id, 3, now)

        start, end = get_day_window(now)
        usage_a = get_usage(db_session, ta.id, start, end)
        usage_b = get_usage(db_session, tb.id, start, end)

        assert usage_a["optimize_job"] == 10
        assert usage_a["apply"] == 0
        assert usage_b["optimize_job"] == 0
        assert usage_b["apply"] == 3


# ===================================================================
# 7. Audit event on quota exceeded
# ===================================================================


class TestQuotaAuditEvent:
    def test_audit_event_recorded_on_block(self, db_session):
        """A quota.exceeded audit event is created when the limit is hit."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=2
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 2, now)

        with pytest.raises(QuotaExceeded):
            check_quota(db_session, t, "optimize_job", now)

        events = (
            db_session.query(AuditEvent)
            .filter(
                AuditEvent.tenant_id == t.id,
                AuditEvent.event_type == "quota.exceeded",
            )
            .all()
        )
        assert len(events) == 1
        meta = json.loads(events[0].meta_json)
        assert meta["action"] == "optimize_job"
        assert meta["used"] == 2
        assert meta["limit"] == 2
        assert "reset_at" in meta

    def test_no_audit_event_when_allowed(self, db_session):
        """No audit event is created when the request is allowed."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=10
        )
        now = datetime(2026, 4, 5, 14, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 5, now)
        check_quota(db_session, t, "optimize_job", now)

        events = (
            db_session.query(AuditEvent)
            .filter(
                AuditEvent.tenant_id == t.id,
                AuditEvent.event_type == "quota.exceeded",
            )
            .all()
        )
        assert len(events) == 0


# ===================================================================
# 8. Window boundary correctness
# ===================================================================


class TestWindowBoundaries:
    def test_start_of_day_inclusive(self, db_session):
        """A row created at exactly 00:00:00 UTC is included in today."""
        t, _u = _seed_tenant_and_user(db_session)
        midnight = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 1, midnight)
        start, end = get_day_window(midnight)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 1

    def test_end_of_day_exclusive(self, db_session):
        """A row created at exactly 00:00:00 of the next day is NOT in today."""
        t, _u = _seed_tenant_and_user(db_session)
        today = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        next_midnight = datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 1, next_midnight)
        start, end = get_day_window(today)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 0

    def test_yesterday_excluded(self, db_session):
        """A row from yesterday 23:59:59 is excluded from today's window."""
        t, _u = _seed_tenant_and_user(db_session)
        yesterday_late = datetime(2026, 4, 4, 23, 59, 59, tzinfo=timezone.utc)
        today = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
        _seed_jobs(db_session, t.id, 1, yesterday_late)
        start, end = get_day_window(today)
        usage = get_usage(db_session, t.id, start, end)
        assert usage["optimize_job"] == 0

    def test_multi_day_span(self, db_session):
        """Jobs spread across multiple days only count for their own day."""
        t, _u = _seed_tenant_and_user(
            db_session, daily_optimize_jobs_limit=3
        )
        day1 = datetime(2026, 4, 4, 10, 0, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 4, 5, 10, 0, 0, tzinfo=timezone.utc)
        day3 = datetime(2026, 4, 6, 10, 0, 0, tzinfo=timezone.utc)

        _seed_jobs(db_session, t.id, 3, day1)
        _seed_jobs(db_session, t.id, 2, day2)
        _seed_jobs(db_session, t.id, 4, day3)

        # Day 2 should have only 2 — under limit
        check_quota(db_session, t, "optimize_job", day2)

        # Day 3 should have 4 — over limit
        with pytest.raises(QuotaExceeded) as exc_info:
            check_quota(db_session, t, "optimize_job", day3)
        assert exc_info.value.used == 4


# ===================================================================
# 9. GET /usage endpoint
# ===================================================================


class TestUsageEndpoint:
    def test_returns_correct_counts_and_limits(
        self, db_session, client_auth_on
    ):
        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.admin,
            daily_optimize_jobs_limit=200,
            daily_apply_limit=50,
        )
        token = _make_token(u)
        now = datetime.now(timezone.utc)
        _seed_jobs(db_session, t.id, 17, now)
        _seed_apply_batches(db_session, t.id, 2, now)

        resp = client_auth_on.get(
            "/usage", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["limits"]["optimize_job"] == 200
        assert data["limits"]["apply"] == 50
        assert data["used"]["optimize_job"] == 17
        assert data["used"]["apply"] == 2
        assert data["remaining"]["optimize_job"] == 183
        assert data["remaining"]["apply"] == 48
        assert "window" in data
        assert "start" in data["window"]
        assert "end" in data["window"]

    def test_unlimited_returns_null(self, db_session, client_auth_on):
        """When limits are NULL, limit and remaining should be null."""
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)

        resp = client_auth_on.get(
            "/usage", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["optimize_job"] is None
        assert data["limits"]["apply"] is None
        assert data["remaining"]["optimize_job"] is None
        assert data["remaining"]["apply"] is None

    def test_viewer_can_access(self, db_session, client_auth_on):
        """Viewer role should be able to see usage."""
        _t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)

        resp = client_auth_on.get(
            "/usage", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200


# ===================================================================
# 10. Auth-off mode
# ===================================================================


class TestAuthOffMode:
    def test_usage_returns_503(self, client_auth_off):
        """GET /usage returns 503 when auth is disabled."""
        resp = client_auth_off.get("/usage")
        assert resp.status_code == 503
        assert "auth" in resp.json()["detail"].lower()

    def test_optimize_endpoint_no_quota_enforcement(
        self, db_session, client_auth_off, monkeypatch
    ):
        """When auth is off, the optimize endpoint does not check quotas."""
        # Verify quotas are not checked by patching check_quota to fail
        # if called
        import backend.quotas as quotas_mod

        original_check = quotas_mod.check_quota

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("check_quota should not be called in auth-off mode")

        monkeypatch.setattr(quotas_mod, "check_quota", _must_not_be_called)

        # The endpoint returns 503 because REDIS_URL is not set (expected)
        resp = client_auth_off.post(
            "/jobs/optimize",
            json={
                "api_username": "u",
                "api_password": "p",
                "site_id": 1,
                "min_coverage_rate": 50.0,
            },
        )
        # 503 from missing Redis is the correct auth-off behavior
        assert resp.status_code == 503

        monkeypatch.setattr(quotas_mod, "check_quota", original_check)


# ===================================================================
# 11. Endpoint-level quota enforcement (POST /jobs/optimize)
# ===================================================================


class TestEndpointQuotaEnforcement:
    def _make_arq_mocks(self):
        """Create mock Redis + Arq pool for the jobs endpoint."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.aclose = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock()
        mock_pool.aclose = AsyncMock()
        return mock_redis, mock_pool

    def test_optimize_blocked_at_limit(self, db_session, client_auth_on):
        """POST /jobs/optimize returns 429 when quota is exceeded."""
        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.operator,
            daily_optimize_jobs_limit=3,
        )
        token = _make_token(u)
        now = datetime.now(timezone.utc)
        _seed_jobs(db_session, t.id, 3, now)

        mock_redis, mock_pool = self._make_arq_mocks()

        with (
            patch("backend.jobs_api._get_redis", return_value=mock_redis),
            patch("backend.jobs_api._get_arq_pool", return_value=mock_pool),
        ):
            resp = client_auth_on.post(
                "/jobs/optimize",
                json={
                    "api_username": "u",
                    "api_password": "p",
                    "site_id": 1,
                    "min_coverage_rate": 50.0,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 429
        assert "Daily limit reached" in resp.json()["detail"]
        assert "optimize_job" in resp.json()["detail"]

    def test_optimize_allowed_under_limit(self, db_session, client_auth_on):
        """POST /jobs/optimize succeeds when under quota."""
        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.operator,
            daily_optimize_jobs_limit=10,
        )
        token = _make_token(u)
        now = datetime.now(timezone.utc)
        _seed_jobs(db_session, t.id, 5, now)

        mock_redis, mock_pool = self._make_arq_mocks()

        with (
            patch("backend.jobs_api._get_redis", return_value=mock_redis),
            patch("backend.jobs_api._get_arq_pool", return_value=mock_pool),
        ):
            resp = client_auth_on.post(
                "/jobs/optimize",
                json={
                    "api_username": "u",
                    "api_password": "p",
                    "site_id": 1,
                    "min_coverage_rate": 50.0,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_apply_blocked_at_limit(self, db_session, client_auth_on, monkeypatch):
        """POST /apply-prices/apply returns 429 when apply quota exceeded."""
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", "true")
        get_settings.cache_clear()

        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.admin,
            daily_apply_limit=2,
        )
        token = _make_token(u)
        now = datetime.now(timezone.utc)
        _seed_apply_batches(db_session, t.id, 2, now)

        resp = client_auth_on.post(
            "/apply-prices/apply",
            json={
                "batch_id": str(uuid.uuid4()),
                "confirm": True,
                "api_username": "u",
                "api_password": "p",
                "site_id": 1,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 429
        assert "Daily limit reached" in resp.json()["detail"]
        assert "apply" in resp.json()["detail"]
