"""Tests for Task 9.2 — Admin diagnostics API.

Covers:
1. ``GET /admin/diagnostics`` — health, config flags, counts.
2. ``GET /admin/tenants`` — paginated tenant list, plan filter.
3. ``GET /admin/tenant/{tenant_id}`` — detail with limits + usage.
4. Auth-off → 503 for all admin endpoints.
5. RBAC: viewer/operator → 403; admin/owner → 200.
6. No secrets or PII in responses.
7. Invalid tenant_id → 404/422.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

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
    HostedShopCredential,
    OptimizationJob,
    Role,
    Tenant,
    User,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()

# Secret-like field names that must NEVER appear in admin responses.
_SECRET_FIELDS = {
    "password_hash",
    "api_password",
    "api_username_enc",
    "api_password_enc",
    "encryption_key",
    "jwt_secret",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "email",
}


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
    email: str = "admin@test.com",
    tenant_name: str = "TestTenant",
    plan: str | None = "pro",
    billing_status: str | None = "active",
    stripe_customer_id: str | None = "cus_test123",
    stripe_subscription_id: str | None = None,
) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=tenant_name,
        plan=plan,
        status="active",
        billing_status=billing_status,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
    )
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(_PASSWORD),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(tenant)
    db.refresh(user)
    return tenant, user


def _make_token(user: User, tenant: Tenant) -> str:
    return create_access_token(
        sub=user.id,
        tenant_id=tenant.id,
        role=user.role.value if isinstance(user.role, Role) else user.role,
        settings=get_settings(),
    )


def _auth_env(**extra):
    """Return env dict for auth-enabled mode."""
    env = {
        "SBOPTIMA_AUTH_REQUIRED": "true",
        "JWT_SECRET": "test-secret-for-admin",
        "ENCRYPTION_KEY": _FERNET_KEY,
        "DATABASE_URL": "",
    }
    env.update(extra)
    return env


def _auth_client(db_session: Session, env: dict, token: str) -> tuple[TestClient, dict]:
    """Return a TestClient with auth enabled and DB overridden, plus headers."""
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers


# ---------------------------------------------------------------------------
# 1) Auth-off → 503
# ---------------------------------------------------------------------------


class TestAdminAuthOff:
    """When auth is disabled, all admin endpoints return 503."""

    def test_diagnostics_503(self):
        with patch.dict(os.environ, {"SBOPTIMA_AUTH_REQUIRED": "false"}, clear=False):
            get_settings.cache_clear()
            client = TestClient(app)
            resp = client.get("/admin/diagnostics")
            assert resp.status_code == 503

    def test_tenants_list_503(self):
        with patch.dict(os.environ, {"SBOPTIMA_AUTH_REQUIRED": "false"}, clear=False):
            get_settings.cache_clear()
            client = TestClient(app)
            resp = client.get("/admin/tenants")
            assert resp.status_code == 503

    def test_tenant_detail_503(self):
        tid = str(uuid.uuid4())
        with patch.dict(os.environ, {"SBOPTIMA_AUTH_REQUIRED": "false"}, clear=False):
            get_settings.cache_clear()
            client = TestClient(app)
            resp = client.get(f"/admin/tenant/{tid}")
            assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 2) RBAC enforcement
# ---------------------------------------------------------------------------


class TestAdminRBAC:
    """Viewer and operator roles must get 403."""

    @pytest.mark.parametrize("role", [Role.viewer, Role.operator])
    def test_viewer_operator_blocked(self, db_session, role):
        with patch.dict(os.environ, _auth_env(), clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session, role=role)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                headers = {"Authorization": f"Bearer {token}"}
                for path in [
                    "/admin/diagnostics",
                    "/admin/tenants",
                    f"/admin/tenant/{tenant.id}",
                ]:
                    resp = client.get(path, headers=headers)
                    assert resp.status_code == 403, f"{path} should be 403 for {role}"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_admin_allowed(self, db_session):
        with patch.dict(os.environ, _auth_env(), clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session, role=Role.admin)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                headers = {"Authorization": f"Bearer {token}"}
                resp = client.get("/admin/diagnostics", headers=headers)
                assert resp.status_code == 200
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_owner_allowed(self, db_session):
        with patch.dict(os.environ, _auth_env(), clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session, role=Role.owner)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                headers = {"Authorization": f"Bearer {token}"}
                resp = client.get("/admin/diagnostics", headers=headers)
                assert resp.status_code == 200
            finally:
                app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 3) GET /admin/diagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    """Diagnostics endpoint returns health, config flags, counts."""

    def test_response_shape(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/diagnostics",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert "version" in data
                assert "config_flags" in data
                assert "db_status" in data
                assert "counts" in data
                assert isinstance(data["config_flags"], dict)
                assert "auth_required" in data["config_flags"]
                assert "billing_enabled" in data["config_flags"]
                assert "metrics_enabled" in data["config_flags"]
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_counts_include_tenants(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/diagnostics",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                # At least 1 tenant (we seeded one)
                assert data["counts"]["tenants"] >= 1
                assert data["counts"]["users"] >= 1
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_git_sha_from_env(self, db_session):
        env = _auth_env()
        env["GIT_SHA"] = "abc123"
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/diagnostics",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                assert data["git_sha"] == "abc123"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_no_secrets_in_response(self, db_session):
        """Diagnostics must not leak secret-like keys."""
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/diagnostics",
                    headers={"Authorization": f"Bearer {token}"},
                )
                raw = resp.text.lower()
                for field in _SECRET_FIELDS:
                    assert field not in raw, f"Secret field '{field}' leaked in diagnostics"
            finally:
                app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 4) GET /admin/tenants
# ---------------------------------------------------------------------------


class TestAdminTenants:
    """Paginated tenant list."""

    def _setup(self, db_session, n_tenants=3):
        tenants = []
        for i in range(n_tenants):
            t = Tenant(
                name=f"Tenant{i}",
                plan="pro" if i % 2 == 0 else "free",
                status="active",
                billing_status="active" if i % 2 == 0 else None,
            )
            db_session.add(t)
            tenants.append(t)
        db_session.flush()

        # Create admin user in the first tenant
        user = User(
            tenant_id=tenants[0].id,
            email="admin@tenants.com",
            password_hash=hash_password(_PASSWORD),
            role=Role.admin,
        )
        db_session.add(user)
        db_session.commit()
        for t in tenants:
            db_session.refresh(t)
        db_session.refresh(user)
        return tenants, user

    def test_list_returns_all(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenants, user = self._setup(db_session)
            token = _make_token(user, tenants[0])

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/tenants",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["total"] == 3
                assert len(data["items"]) == 3
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_pagination(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenants, user = self._setup(db_session, n_tenants=5)
            token = _make_token(user, tenants[0])

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/tenants?limit=2&offset=0",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                assert data["total"] == 5
                assert len(data["items"]) == 2
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_plan_filter(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenants, user = self._setup(db_session, n_tenants=4)
            token = _make_token(user, tenants[0])

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/tenants?plan=free",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                for item in data["items"]:
                    assert item["plan"] == "free"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_no_email_in_tenant_list(self, db_session):
        """Tenant list must not expose user emails."""
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenants, user = self._setup(db_session)
            token = _make_token(user, tenants[0])

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/tenants",
                    headers={"Authorization": f"Bearer {token}"},
                )
                raw = resp.text.lower()
                assert "email" not in raw
            finally:
                app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# 5) GET /admin/tenant/{tenant_id}
# ---------------------------------------------------------------------------


class TestAdminTenantDetail:
    """Single tenant detail endpoint."""

    def _setup(self, db_session):
        tenant, user = _seed_tenant_and_user(
            db_session,
            plan="pro",
            stripe_customer_id="cus_abc",
            stripe_subscription_id="sub_xyz",
            billing_status="active",
        )
        return tenant, user

    def test_detail_response(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = self._setup(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    f"/admin/tenant/{tenant.id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["id"] == str(tenant.id)
                assert data["name"] == tenant.name
                assert data["plan"] == "pro"
                assert data["has_stripe_customer"] is True
                assert data["has_stripe_subscription"] is True
                assert "limits" in data
                assert "usage" in data
                assert data["user_count"] >= 1
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_detail_no_secrets(self, db_session):
        """Tenant detail must not leak secrets or PII."""
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = self._setup(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    f"/admin/tenant/{tenant.id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                raw = resp.text.lower()
                for field in _SECRET_FIELDS:
                    assert field not in raw, f"Secret '{field}' leaked in tenant detail"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_not_found(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)
            fake_id = uuid.uuid4()

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    f"/admin/tenant/{fake_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 404
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_invalid_uuid(self, db_session):
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    "/admin/tenant/not-a-uuid",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 422
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_billing_booleans_false_when_missing(self, db_session):
        """When Stripe fields are NULL, booleans should be False."""
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = _seed_tenant_and_user(
                db_session,
                stripe_customer_id=None,
                stripe_subscription_id=None,
            )
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    f"/admin/tenant/{tenant.id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                assert data["has_stripe_customer"] is False
                assert data["has_stripe_subscription"] is False
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_usage_snapshot_present(self, db_session):
        """Usage snapshot should contain expected keys."""
        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            tenant, user = self._setup(db_session)
            token = _make_token(user, tenant)

            def _override():
                yield db_session

            app.dependency_overrides[get_db] = _override
            try:
                client = TestClient(app)
                resp = client.get(
                    f"/admin/tenant/{tenant.id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                assert "optimize_job" in data["usage"]
                assert "apply" in data["usage"]
                assert "optimize_sync" in data["usage"]
            finally:
                app.dependency_overrides.pop(get_db, None)
