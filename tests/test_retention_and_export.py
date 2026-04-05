"""Tests for Task 10.2 — Data retention + tenant export.

Covers:
1. Retention prunes records older than cutoff.
2. Retention does NOT touch newer records.
3. Retention disabled → no-op.
4. Export does NOT include secret fields (password_hash, api_password_enc, etc.).
5. Export is tenant-specific and returns expected counts.
6. Export emits audit event ``admin.tenant.exported``.
7. Auth-off → 503 for export endpoint.
8. RBAC: viewer/operator → 403; admin/owner → 200.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
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
    ApplyBatch,
    AuditEvent,
    HostedShopCredential,
    OptimizationJob,
    Role,
    Tenant,
    User,
)
from backend.retention import prune_audit, prune_batches, prune_jobs, run_retention

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()

# Secret-like field names that must NEVER appear in export responses.
_SECRET_FIELDS = {
    "password_hash",
    "api_password_enc",
    "api_username_enc",
    "encryption_key",
    "stripe_secret_key",
    "stripe_webhook_secret",
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


def _seed(
    db: Session,
    *,
    role: Role = Role.admin,
    tenant_name: str = "ExportTenant",
) -> tuple[Tenant, User]:
    """Seed a tenant + user and return them."""
    tenant = Tenant(name=tenant_name, plan="pro", status="active")
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email="admin@test.com",
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
    env = {
        "SBOPTIMA_AUTH_REQUIRED": "true",
        "JWT_SECRET": "test-secret-retention",
        "ENCRYPTION_KEY": _FERNET_KEY,
        "DATABASE_URL": "",
    }
    env.update(extra)
    return env


def _auth_client(db_session: Session, env: dict, token: str) -> tuple[TestClient, dict]:
    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers


# ---------------------------------------------------------------------------
# Helper: seed timed records
# ---------------------------------------------------------------------------

NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(days=60)
VERY_OLD = NOW - timedelta(days=120)
RECENT = NOW - timedelta(days=5)


def _seed_records(db: Session, tenant: Tenant, user: User):
    """Seed a mix of old and recent jobs, batches, and audit events."""
    # Old jobs (should be pruned with 30-day retention)
    for i in range(3):
        db.add(
            OptimizationJob(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                status="completed",
                created_at=OLD - timedelta(days=i),
                request_json=json.dumps({"test": True}),
            )
        )
    # Recent jobs (should NOT be pruned)
    for i in range(2):
        db.add(
            OptimizationJob(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                status="completed",
                created_at=RECENT + timedelta(hours=i),
                request_json=json.dumps({"test": True}),
            )
        )
    # Old batches
    for i in range(2):
        db.add(
            ApplyBatch(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                mode="dry_run",
                status="completed",
                created_at=OLD - timedelta(days=i),
            )
        )
    # Recent batch
    db.add(
        ApplyBatch(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            user_id=user.id,
            mode="dry_run",
            status="completed",
            created_at=RECENT,
        )
    )
    # Old audit events
    for i in range(4):
        db.add(
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                event_type="test.old",
                created_at=VERY_OLD - timedelta(days=i),
            )
        )
    # Recent audit events
    for i in range(2):
        db.add(
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                user_id=user.id,
                event_type="test.recent",
                created_at=RECENT + timedelta(hours=i),
            )
        )
    db.commit()


# ===========================================================================
# Retention tests
# ===========================================================================


class TestRetentionPruning:
    """Retention prunes old records and leaves newer ones intact."""

    def test_prune_jobs_deletes_old(self, db_session):
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        # 5 total jobs: 3 old + 2 recent
        assert db_session.query(OptimizationJob).count() == 5

        cutoff = NOW - timedelta(days=30)
        deleted = prune_jobs(db_session, cutoff)
        db_session.commit()

        assert deleted == 3
        assert db_session.query(OptimizationJob).count() == 2

    def test_prune_batches_deletes_old(self, db_session):
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        # 3 total batches: 2 old + 1 recent
        assert db_session.query(ApplyBatch).count() == 3

        cutoff = NOW - timedelta(days=30)
        deleted = prune_batches(db_session, cutoff)
        db_session.commit()

        assert deleted == 2
        assert db_session.query(ApplyBatch).count() == 1

    def test_prune_audit_deletes_old(self, db_session):
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        # 6 total audit: 4 very old + 2 recent
        assert db_session.query(AuditEvent).count() == 6

        cutoff = NOW - timedelta(days=90)
        deleted = prune_audit(db_session, cutoff)
        db_session.commit()

        assert deleted == 4
        assert db_session.query(AuditEvent).count() == 2

    def test_prune_does_not_touch_newer_records(self, db_session):
        """With a far-past cutoff, nothing should be deleted."""
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        very_old_cutoff = NOW - timedelta(days=365)
        assert prune_jobs(db_session, very_old_cutoff) == 0
        assert prune_batches(db_session, very_old_cutoff) == 0
        assert prune_audit(db_session, very_old_cutoff) == 0

    def test_run_retention_full(self, db_session):
        """run_retention applies all three prune functions and emits audit event."""
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        settings = SimpleNamespace(
            retention_enabled=True,
            retention_jobs_days=30,
            retention_batches_days=30,
            retention_audit_days=90,
        )

        result = run_retention(db_session, settings, now_utc=NOW)

        assert result["pruned_counts"]["jobs"] == 3
        assert result["pruned_counts"]["batches"] == 2
        assert result["pruned_counts"]["audit"] == 4

        # Verify maintenance audit event was emitted
        maint_events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == "maintenance.retention_ran")
            .all()
        )
        assert len(maint_events) == 1
        meta = json.loads(maint_events[0].meta_json)
        assert "pruned_counts" in meta
        assert "cutoffs" in meta

    def test_run_retention_disabled(self, db_session):
        """When retention_enabled=False, nothing is pruned."""
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        settings = SimpleNamespace(
            retention_enabled=False,
            retention_jobs_days=30,
            retention_batches_days=30,
            retention_audit_days=90,
        )

        result = run_retention(db_session, settings, now_utc=NOW)
        assert result == {"cutoffs": {}, "pruned_counts": {}}

        # Nothing deleted
        assert db_session.query(OptimizationJob).count() == 5
        assert db_session.query(ApplyBatch).count() == 3
        assert db_session.query(AuditEvent).count() == 6


# ===========================================================================
# Export tests
# ===========================================================================


class TestTenantExport:
    """GET /admin/tenant/{tenant_id}/export."""

    def test_export_returns_expected_data(self, db_session):
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(user, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

            data = resp.json()
            assert data["tenant"]["id"] == str(tenant.id)
            assert data["tenant"]["name"] == "ExportTenant"
            assert len(data["users"]) == 1
            assert len(data["jobs"]) == 5
            assert len(data["batches"]) == 3
            # audit events: 6 seeded + 1 from export itself
            assert len(data["audit"]) >= 6

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_export_no_secret_fields(self, db_session):
        """No secret fields (password_hash, api_*_enc, etc.) in export."""
        tenant, user = _seed(db_session)
        _seed_records(db_session, tenant, user)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(user, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

            raw = resp.text
            for field in _SECRET_FIELDS:
                assert field not in raw, f"Secret field '{field}' leaked in export"

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_export_is_tenant_specific(self, db_session):
        """Export only returns data for the requested tenant."""
        t1, u1 = _seed(db_session, tenant_name="Tenant1")
        t2 = Tenant(name="Tenant2", plan="free", status="active")
        db_session.add(t2)
        db_session.flush()
        u2 = User(
            tenant_id=t2.id,
            email="user2@test.com",
            password_hash=hash_password(_PASSWORD),
            role=Role.operator,
        )
        db_session.add(u2)
        db_session.commit()
        db_session.refresh(t2)
        db_session.refresh(u2)

        # Add jobs to both tenants
        for _ in range(3):
            db_session.add(
                OptimizationJob(
                    tenant_id=t1.id, user_id=u1.id,
                    status="completed", created_at=RECENT,
                )
            )
        for _ in range(5):
            db_session.add(
                OptimizationJob(
                    tenant_id=t2.id, user_id=u2.id,
                    status="completed", created_at=RECENT,
                )
            )
        db_session.commit()

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(u1, t1)
            client, headers = _auth_client(db_session, env, token)

            # Export tenant 1 — should see 3 jobs
            resp = client.get(f"/admin/tenant/{t1.id}/export", headers=headers)
            assert resp.status_code == 200
            assert len(resp.json()["jobs"]) == 3

            # Export tenant 2 — should see 5 jobs
            resp = client.get(f"/admin/tenant/{t2.id}/export", headers=headers)
            assert resp.status_code == 200
            assert len(resp.json()["jobs"]) == 5

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_export_emits_audit_event(self, db_session):
        """Export creates an admin.tenant.exported audit event."""
        tenant, user = _seed(db_session)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(user, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

        # Check the audit event was recorded in the DB
        events = (
            db_session.query(AuditEvent)
            .filter(AuditEvent.event_type == "admin.tenant.exported")
            .all()
        )
        assert len(events) == 1
        meta = json.loads(events[0].meta_json)
        assert meta["tenant_id"] == str(tenant.id)

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_export_404_nonexistent_tenant(self, db_session):
        """Export returns 404 for a nonexistent tenant."""
        tenant, user = _seed(db_session)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(user, tenant)
            client, headers = _auth_client(db_session, env, token)

            fake_id = str(uuid.uuid4())
            resp = client.get(f"/admin/tenant/{fake_id}/export", headers=headers)
            assert resp.status_code == 404

        get_settings.cache_clear()
        app.dependency_overrides.clear()


class TestExportAuthOff:
    """When auth is disabled, export returns 503."""

    def test_export_503_auth_off(self):
        tid = str(uuid.uuid4())
        with patch.dict(os.environ, {"SBOPTIMA_AUTH_REQUIRED": "false"}, clear=False):
            get_settings.cache_clear()
            client = TestClient(app)
            resp = client.get(f"/admin/tenant/{tid}/export")
            assert resp.status_code == 503

        get_settings.cache_clear()
        app.dependency_overrides.clear()


class TestExportRBAC:
    """RBAC: viewer → 403, operator → 403, admin → 200."""

    def test_viewer_forbidden(self, db_session):
        tenant, _ = _seed(db_session)
        viewer = User(
            tenant_id=tenant.id,
            email="viewer@test.com",
            password_hash=hash_password(_PASSWORD),
            role=Role.viewer,
        )
        db_session.add(viewer)
        db_session.commit()
        db_session.refresh(viewer)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(viewer, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 403

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_operator_forbidden(self, db_session):
        tenant, _ = _seed(db_session)
        operator = User(
            tenant_id=tenant.id,
            email="operator@test.com",
            password_hash=hash_password(_PASSWORD),
            role=Role.operator,
        )
        db_session.add(operator)
        db_session.commit()
        db_session.refresh(operator)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(operator, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 403

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_admin_allowed(self, db_session):
        tenant, admin = _seed(db_session, role=Role.admin)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(admin, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

        get_settings.cache_clear()
        app.dependency_overrides.clear()

    def test_owner_allowed(self, db_session):
        tenant, _ = _seed(db_session)
        owner = User(
            tenant_id=tenant.id,
            email="owner@test.com",
            password_hash=hash_password(_PASSWORD),
            role=Role.owner,
        )
        db_session.add(owner)
        db_session.commit()
        db_session.refresh(owner)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(owner, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

        get_settings.cache_clear()
        app.dependency_overrides.clear()


class TestExportUserFieldSafety:
    """Ensure user records in export never contain password_hash."""

    def test_user_no_password_hash(self, db_session):
        tenant, user = _seed(db_session)

        env = _auth_env()
        with patch.dict(os.environ, env, clear=False):
            get_settings.cache_clear()
            token = _make_token(user, tenant)
            client, headers = _auth_client(db_session, env, token)

            resp = client.get(f"/admin/tenant/{tenant.id}/export", headers=headers)
            assert resp.status_code == 200

            for u in resp.json()["users"]:
                assert "password_hash" not in u
                assert "email" not in u  # PII should not be in export

        get_settings.cache_clear()
        app.dependency_overrides.clear()
