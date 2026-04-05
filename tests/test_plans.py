"""Tests for Task 7.2 — Plans + billing scaffolding (Stripe-ready).

Covers:
1. Plan definitions: list_plans, get_plan helpers.
2. ``GET /plans`` returns expected plans.
3. ``GET /tenant/plan`` returns current plan and effective limits.
4. ``PUT /tenant/plan`` updates plan, emits audit event.
5. ``get_limits()`` precedence:
   - plan defaults used when overrides null
   - overrides win when set
   - no plan and no overrides → unlimited
6. RBAC: viewer cannot change plan; admin/owner can.
7. Auth-off: plan endpoints return 503.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

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
from backend.plans import PLANS, Plan, get_plan, list_plans
from backend.quotas import get_limits

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
    role: Role = Role.admin,
    email: str = "user@t1.com",
    tenant_name: str = "Tenant-1",
    plan: str | None = None,
    daily_optimize_jobs_limit: int | None = None,
    daily_apply_limit: int | None = None,
    daily_optimize_sync_limit: int | None = None,
) -> tuple[Tenant, User]:
    """Create and return a (Tenant, User) pair."""
    t = Tenant(
        id=uuid.uuid4(),
        name=tenant_name,
        plan=plan,
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


# ===========================================================================
# 1. Plan definitions (unit tests)
# ===========================================================================


class TestPlanDefinitions:
    """Pure unit tests for backend/plans.py."""

    def test_plans_dict_has_expected_keys(self):
        assert set(PLANS.keys()) == {"free", "pro", "enterprise"}

    def test_free_plan_limits(self):
        p = PLANS["free"]
        assert p.name == "free"
        assert p.daily_optimize_jobs_limit == 25
        assert p.daily_apply_limit == 0
        assert p.daily_optimize_sync_limit == 10

    def test_pro_plan_limits(self):
        p = PLANS["pro"]
        assert p.name == "pro"
        assert p.daily_optimize_jobs_limit == 200
        assert p.daily_apply_limit == 50
        assert p.daily_optimize_sync_limit == 200

    def test_enterprise_plan_unlimited(self):
        p = PLANS["enterprise"]
        assert p.name == "enterprise"
        assert p.daily_optimize_jobs_limit is None
        assert p.daily_apply_limit is None
        assert p.daily_optimize_sync_limit is None

    def test_get_plan_found(self):
        assert get_plan("free") == PLANS["free"]
        assert get_plan("pro") == PLANS["pro"]
        assert get_plan("enterprise") == PLANS["enterprise"]

    def test_get_plan_case_insensitive(self):
        assert get_plan("Free") == PLANS["free"]
        assert get_plan("PRO") == PLANS["pro"]

    def test_get_plan_not_found(self):
        assert get_plan("unknown") is None
        assert get_plan("") is None
        assert get_plan(None) is None  # type: ignore[arg-type]

    def test_list_plans_returns_all(self):
        plans = list_plans()
        assert len(plans) == 3
        names = {p.name for p in plans}
        assert names == {"free", "pro", "enterprise"}

    def test_plan_to_dict(self):
        d = PLANS["free"].to_dict()
        assert d == {
            "name": "free",
            "daily_optimize_jobs_limit": 25,
            "daily_apply_limit": 0,
            "daily_optimize_sync_limit": 10,
        }

    def test_plan_is_frozen(self):
        with pytest.raises(AttributeError):
            PLANS["free"].name = "modified"  # type: ignore[misc]


# ===========================================================================
# 2. get_limits() precedence (unit tests with DB)
# ===========================================================================


class TestGetLimitsPrecedence:
    """Test that get_limits() applies the plan → override precedence."""

    def test_no_plan_no_overrides_unlimited(self, db_session):
        """No plan, no explicit limits → all None (unlimited)."""
        t, _ = _seed_tenant_and_user(db_session)
        limits = get_limits(t)
        assert limits == {
            "optimize_job": None,
            "apply": None,
            "optimize_sync": None,
        }

    def test_plan_defaults_used_when_overrides_null(self, db_session):
        """Plan set, no overrides → plan defaults."""
        t, _ = _seed_tenant_and_user(db_session, plan="free")
        limits = get_limits(t)
        assert limits == {
            "optimize_job": 25,
            "apply": 0,
            "optimize_sync": 10,
        }

    def test_pro_plan_defaults(self, db_session):
        t, _ = _seed_tenant_and_user(db_session, plan="pro")
        limits = get_limits(t)
        assert limits == {
            "optimize_job": 200,
            "apply": 50,
            "optimize_sync": 200,
        }

    def test_enterprise_plan_unlimited(self, db_session):
        t, _ = _seed_tenant_and_user(db_session, plan="enterprise")
        limits = get_limits(t)
        assert limits == {
            "optimize_job": None,
            "apply": None,
            "optimize_sync": None,
        }

    def test_overrides_win_over_plan(self, db_session):
        """Explicit limits override plan defaults."""
        t, _ = _seed_tenant_and_user(
            db_session,
            plan="free",
            daily_optimize_jobs_limit=100,
            daily_apply_limit=10,
            daily_optimize_sync_limit=50,
        )
        limits = get_limits(t)
        assert limits == {
            "optimize_job": 100,
            "apply": 10,
            "optimize_sync": 50,
        }

    def test_partial_override(self, db_session):
        """Some overrides set, others fall back to plan defaults."""
        t, _ = _seed_tenant_and_user(
            db_session,
            plan="pro",
            daily_optimize_jobs_limit=999,
            # daily_apply_limit not set → use pro default (50)
            # daily_optimize_sync_limit not set → use pro default (200)
        )
        limits = get_limits(t)
        assert limits["optimize_job"] == 999
        assert limits["apply"] == 50
        assert limits["optimize_sync"] == 200

    def test_unknown_plan_treated_as_none(self, db_session):
        """Unknown plan name → treated like no plan (unlimited)."""
        t, _ = _seed_tenant_and_user(db_session, plan="nonexistent")
        limits = get_limits(t)
        assert limits == {
            "optimize_job": None,
            "apply": None,
            "optimize_sync": None,
        }

    def test_override_zero_wins_over_plan(self, db_session):
        """Explicit 0 override should still apply (not fall back to plan)."""
        t, _ = _seed_tenant_and_user(
            db_session,
            plan="enterprise",
            daily_apply_limit=0,
        )
        limits = get_limits(t)
        assert limits["apply"] == 0


# ===========================================================================
# 3. GET /plans
# ===========================================================================


class TestGetPlansEndpoint:
    def test_returns_plans(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get("/plans", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert "plans" in data
        names = {p["name"] for p in data["plans"]}
        assert names == {"free", "pro", "enterprise"}

    def test_plan_structure(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get("/plans", headers={"Authorization": f"Bearer {token}"})
        data = resp.json()
        free = next(p for p in data["plans"] if p["name"] == "free")
        assert free["daily_optimize_jobs_limit"] == 25
        assert free["daily_apply_limit"] == 0
        assert free["daily_optimize_sync_limit"] == 10

    def test_auth_off_returns_503(self, db_session, client_auth_off):
        resp = client_auth_off.get("/plans")
        assert resp.status_code == 503


# ===========================================================================
# 4. GET /tenant/plan
# ===========================================================================


class TestGetTenantPlan:
    def test_returns_current_plan_and_limits(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer, plan="pro")
        token = _make_token(u)
        resp = client_auth_on.get(
            "/tenant/plan", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "pro"
        assert data["effective_limits"]["optimize_job"] == 200
        assert data["effective_limits"]["apply"] == 50
        assert data["effective_limits"]["optimize_sync"] == 200

    def test_no_plan_returns_null_and_unlimited(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/tenant/plan", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] is None
        assert data["effective_limits"]["optimize_job"] is None

    def test_auth_off_returns_503(self, db_session, client_auth_off):
        resp = client_auth_off.get("/tenant/plan")
        assert resp.status_code == 503


# ===========================================================================
# 5. PUT /tenant/plan
# ===========================================================================


class TestUpdateTenantPlan:
    def test_update_plan_success(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "pro"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "pro"
        assert data["effective_limits"]["optimize_job"] == 200

    def test_update_plan_emits_audit_event(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "free"},
        )
        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == "tenant.plan.updated")
            .all()
        )
        assert len(events) == 1
        meta = json.loads(events[0].meta_json)
        assert meta["old"] is None
        assert meta["new"] == "free"

    def test_update_plan_twice_tracks_old(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "free"},
        )
        client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "pro"},
        )
        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == "tenant.plan.updated")
            .order_by(AuditEvent.created_at)
            .all()
        )
        assert len(events) == 2
        meta1 = json.loads(events[0].meta_json)
        meta2 = json.loads(events[1].meta_json)
        assert meta1 == {"old": None, "new": "free"}
        assert meta2 == {"old": "free", "new": "pro"}

    def test_set_plan_to_null(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin, plan="pro")
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": None},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] is None

    def test_unknown_plan_rejected(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "platinum"},
        )
        assert resp.status_code == 422

    def test_case_insensitive_plan_name(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.admin)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "Pro"},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] == "pro"

    def test_owner_can_update_plan(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.owner)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "enterprise"},
        )
        assert resp.status_code == 200
        assert resp.json()["plan"] == "enterprise"

    def test_auth_off_returns_503(self, db_session, client_auth_off):
        resp = client_auth_off.put("/tenant/plan", json={"plan": "free"})
        assert resp.status_code == 503


# ===========================================================================
# 6. RBAC: viewer/operator cannot change plan
# ===========================================================================


class TestPlanRBAC:
    def test_viewer_cannot_put_plan(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "pro"},
        )
        assert resp.status_code == 403

    def test_operator_cannot_put_plan(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)
        resp = client_auth_on.put(
            "/tenant/plan",
            headers={"Authorization": f"Bearer {token}"},
            json={"plan": "pro"},
        )
        assert resp.status_code == 403

    def test_viewer_can_get_plans(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/plans", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200

    def test_viewer_can_get_tenant_plan(self, db_session, client_auth_on):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/tenant/plan", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200


# ===========================================================================
# 7. Usage endpoint reflects plan-based limits
# ===========================================================================


class TestUsageReflectsPlanLimits:
    def test_usage_shows_plan_limits(self, db_session, client_auth_on):
        """GET /usage should reflect effective limits from plan."""
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer, plan="free")
        token = _make_token(u)
        resp = client_auth_on.get(
            "/usage", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["optimize_job"] == 25
        assert data["limits"]["apply"] == 0
        assert data["limits"]["optimize_sync"] == 10

    def test_usage_shows_override_over_plan(self, db_session, client_auth_on):
        """GET /usage should reflect explicit overrides over plan."""
        t, u = _seed_tenant_and_user(
            db_session,
            role=Role.viewer,
            plan="free",
            daily_optimize_jobs_limit=999,
        )
        token = _make_token(u)
        resp = client_auth_on.get(
            "/usage", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["limits"]["optimize_job"] == 999
        # Other limits still come from plan
        assert data["limits"]["apply"] == 0
        assert data["limits"]["optimize_sync"] == 10
