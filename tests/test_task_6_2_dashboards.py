"""Tests for Task 6.2 — Tenant dashboards (list endpoints + History UI).

Covers:
1. Repository layer: list_jobs, list_batches, list_audit_events with filters.
2. API endpoints (auth ON): GET /jobs, GET /apply-prices/batches, GET /audit.
3. Pagination (limit/offset) and filtering (status, mode, event_type, since/until).
4. Tenant isolation: tenant A cannot list tenant B items.
5. Auth-off behaviour: all three list endpoints return 503.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

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
from backend.repositories import batches_repo, jobs_repo
from backend.repositories import audit_repo

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
) -> tuple[Tenant, User]:
    """Create and return a (Tenant, User) pair."""
    t = Tenant(id=uuid.uuid4(), name=tenant_name)
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
    return create_access_token(sub=user.id, tenant_id=user.tenant_id, role=user.role.value)


def _make_client(
    db_session: Session,
    monkeypatch,
    *,
    auth_required: bool,
):
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false")
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
    yield from _make_client(db_session, monkeypatch, auth_required=True)


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    yield from _make_client(db_session, monkeypatch, auth_required=False)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_jobs(db: Session, tenant: Tenant, user: User, count: int = 5):
    """Seed *count* OptimizationJob rows with varying statuses."""
    statuses = ["queued", "running", "completed", "failed"]
    base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    jobs = []
    for i in range(count):
        j = OptimizationJob(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            user_id=user.id,
            status=statuses[i % len(statuses)],
            created_at=base_time + timedelta(hours=i),
        )
        db.add(j)
        jobs.append(j)
    db.commit()
    return jobs


def _seed_batches(db: Session, tenant: Tenant, user: User, count: int = 5):
    """Seed *count* ApplyBatch rows with varying modes/statuses."""
    modes = ["dry_run", "apply", "create_manifest"]
    statuses = ["created", "running", "completed", "failed"]
    base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    batches = []
    for i in range(count):
        b = ApplyBatch(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            user_id=user.id,
            mode=modes[i % len(modes)],
            status=statuses[i % len(statuses)],
            created_at=base_time + timedelta(hours=i),
        )
        db.add(b)
        batches.append(b)
    db.commit()
    return batches


def _seed_audit_events(db: Session, tenant: Tenant, user: User, count: int = 5):
    """Seed *count* AuditEvent rows with varying event types."""
    types = ["job.enqueued", "job.completed", "apply.dry_run.completed", "apply.apply.completed"]
    base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    events = []
    for i in range(count):
        e = AuditEvent(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            user_id=user.id,
            event_type=types[i % len(types)],
            created_at=base_time + timedelta(hours=i),
            meta_json=json.dumps({"index": i}),
        )
        db.add(e)
        events.append(e)
    db.commit()
    return events


# ===================================================================
# 1. Repository layer tests
# ===================================================================


class TestJobsRepoFiltered:
    def test_list_returns_total_and_items(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_jobs(db_session, t, u, count=10)
        total, items = jobs_repo.list_jobs(db_session, tenant_id=t.id, limit=5)
        assert total == 10
        assert len(items) == 5

    def test_list_filter_by_status(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_jobs(db_session, t, u, count=8)
        total, items = jobs_repo.list_jobs(
            db_session, tenant_id=t.id, status="completed"
        )
        assert total == 2  # index 2 and 6 → "completed"
        assert all(j.status == "completed" for j in items)

    def test_list_filter_by_since(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_jobs(db_session, t, u, count=5)
        cutoff = datetime(2025, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
        total, items = jobs_repo.list_jobs(db_session, tenant_id=t.id, since=cutoff)
        # jobs created at 12, 13, 14, 15, 16 → >=14 → 3 items
        assert total == 3

    def test_list_filter_by_until(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_jobs(db_session, t, u, count=5)
        cutoff = datetime(2025, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
        total, items = jobs_repo.list_jobs(db_session, tenant_id=t.id, until=cutoff)
        # jobs at 12, 13 → 2
        assert total == 2

    def test_offset_pagination(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_jobs(db_session, t, u, count=10)
        total, page1 = jobs_repo.list_jobs(db_session, tenant_id=t.id, limit=3, offset=0)
        total2, page2 = jobs_repo.list_jobs(db_session, tenant_id=t.id, limit=3, offset=3)
        assert total == 10
        assert total2 == 10
        assert len(page1) == 3
        assert len(page2) == 3
        ids1 = {j.id for j in page1}
        ids2 = {j.id for j in page2}
        assert ids1.isdisjoint(ids2)


class TestBatchesRepoFiltered:
    def test_list_returns_total_and_items(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_batches(db_session, t, u, count=8)
        total, items = batches_repo.list_batches(db_session, tenant_id=t.id, limit=3)
        assert total == 8
        assert len(items) == 3

    def test_list_filter_by_status(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_batches(db_session, t, u, count=8)
        total, items = batches_repo.list_batches(
            db_session, tenant_id=t.id, status="completed"
        )
        assert total == 2
        assert all(b.status == "completed" for b in items)

    def test_list_filter_by_mode(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_batches(db_session, t, u, count=9)
        total, items = batches_repo.list_batches(
            db_session, tenant_id=t.id, mode="dry_run"
        )
        assert total == 3  # indices 0, 3, 6
        assert all(b.mode == "dry_run" for b in items)

    def test_offset_pagination(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_batches(db_session, t, u, count=10)
        _, page1 = batches_repo.list_batches(db_session, tenant_id=t.id, limit=4, offset=0)
        _, page2 = batches_repo.list_batches(db_session, tenant_id=t.id, limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 4
        ids1 = {b.id for b in page1}
        ids2 = {b.id for b in page2}
        assert ids1.isdisjoint(ids2)


class TestAuditRepoFiltered:
    def test_list_returns_total_and_items(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_audit_events(db_session, t, u, count=7)
        total, items = audit_repo.list_audit_events(db_session, tenant_id=t.id, limit=3)
        assert total == 7
        assert len(items) == 3

    def test_list_filter_by_event_type(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_audit_events(db_session, t, u, count=8)
        total, items = audit_repo.list_audit_events(
            db_session, tenant_id=t.id, event_type="job.enqueued"
        )
        assert total == 2  # indices 0 and 4
        assert all(e.event_type == "job.enqueued" for e in items)

    def test_list_filter_by_since(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_audit_events(db_session, t, u, count=5)
        cutoff = datetime(2025, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        total, items = audit_repo.list_audit_events(
            db_session, tenant_id=t.id, since=cutoff
        )
        # events at 12, 13, 14, 15, 16 → >=15 → 2 items
        assert total == 2

    def test_offset_pagination(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        _seed_audit_events(db_session, t, u, count=10)
        _, page1 = audit_repo.list_audit_events(db_session, tenant_id=t.id, limit=4, offset=0)
        _, page2 = audit_repo.list_audit_events(db_session, tenant_id=t.id, limit=4, offset=4)
        assert len(page1) == 4
        assert len(page2) == 4
        ids1 = {e.id for e in page1}
        ids2 = {e.id for e in page2}
        assert ids1.isdisjoint(ids2)


# ===================================================================
# 2. Tenant isolation (repository layer)
# ===================================================================


class TestTenantIsolationRepo:
    def test_jobs_isolation(self, db_session):
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1")
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2")
        _seed_jobs(db_session, t1, u1, count=5)
        _seed_jobs(db_session, t2, u2, count=3)
        total1, _ = jobs_repo.list_jobs(db_session, tenant_id=t1.id)
        total2, _ = jobs_repo.list_jobs(db_session, tenant_id=t2.id)
        assert total1 == 5
        assert total2 == 3

    def test_batches_isolation(self, db_session):
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1")
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2")
        _seed_batches(db_session, t1, u1, count=4)
        _seed_batches(db_session, t2, u2, count=6)
        total1, _ = batches_repo.list_batches(db_session, tenant_id=t1.id)
        total2, _ = batches_repo.list_batches(db_session, tenant_id=t2.id)
        assert total1 == 4
        assert total2 == 6

    def test_audit_isolation(self, db_session):
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1")
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2")
        _seed_audit_events(db_session, t1, u1, count=7)
        _seed_audit_events(db_session, t2, u2, count=2)
        total1, _ = audit_repo.list_audit_events(db_session, tenant_id=t1.id)
        total2, _ = audit_repo.list_audit_events(db_session, tenant_id=t2.id)
        assert total1 == 7
        assert total2 == 2


# ===================================================================
# 3. API endpoint tests — auth ON
# ===================================================================


class TestGetJobsEndpoint:
    def test_list_jobs_returns_paginated(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=10)
        resp = client_auth_on.get(
            "/jobs?limit=3&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 10
        assert len(body["items"]) == 3

    def test_list_jobs_filter_by_status(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=8)
        resp = client_auth_on.get(
            "/jobs?status=completed",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert all(j["status"] == "completed" for j in body["items"])

    def test_list_jobs_filter_by_since(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=5)
        resp = client_auth_on.get(
            "/jobs?since=2025-06-01T14:00:00%2B00:00",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3

    def test_list_jobs_offset_page2(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=10)
        resp1 = client_auth_on.get(
            "/jobs?limit=4&offset=0",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp2 = client_auth_on.get(
            "/jobs?limit=4&offset=4",
            headers={"Authorization": f"Bearer {token}"},
        )
        ids1 = {j["id"] for j in resp1.json()["items"]}
        ids2 = {j["id"] for j in resp2.json()["items"]}
        assert ids1.isdisjoint(ids2)

    def test_list_jobs_does_not_return_result_json(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        for item in body["items"]:
            assert "result" not in item
            assert "result_json" not in item
            assert "request_json" not in item

    def test_list_jobs_tenant_isolation(self, client_auth_on, db_session):
        """User from tenant A sees 0 of tenant B's jobs."""
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1", role=Role.viewer)
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2", role=Role.viewer)
        _seed_jobs(db_session, t1, u1, count=3)
        _seed_jobs(db_session, t2, u2, count=5)
        token_a = _make_token(u1)
        resp = client_auth_on.get(
            "/jobs",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        body = resp.json()
        assert body["total"] == 3

    def test_limit_clamped_to_200(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/jobs?limit=999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


class TestGetBatchesEndpoint:
    def test_list_batches_returns_paginated(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_batches(db_session, t, u, count=8)
        resp = client_auth_on.get(
            "/apply-prices/batches?limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 8
        assert len(body["items"]) == 3

    def test_list_batches_filter_by_status(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_batches(db_session, t, u, count=8)
        resp = client_auth_on.get(
            "/apply-prices/batches?status=completed",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["total"] == 2
        assert all(b["status"] == "completed" for b in body["items"])

    def test_list_batches_filter_by_mode(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_batches(db_session, t, u, count=9)
        resp = client_auth_on.get(
            "/apply-prices/batches?mode=dry_run",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["total"] == 3
        assert all(b["mode"] == "dry_run" for b in body["items"])

    def test_list_batches_does_not_return_manifest_or_summary(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_batches(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/apply-prices/batches",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        for item in body["items"]:
            assert "manifest_json" not in item
            assert "summary_json" not in item

    def test_list_batches_tenant_isolation(self, client_auth_on, db_session):
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1", role=Role.viewer)
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2", role=Role.viewer)
        _seed_batches(db_session, t1, u1, count=4)
        _seed_batches(db_session, t2, u2, count=6)
        token_a = _make_token(u1)
        resp = client_auth_on.get(
            "/apply-prices/batches",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        body = resp.json()
        assert body["total"] == 4


class TestGetAuditEndpoint:
    def test_list_audit_returns_paginated(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_audit_events(db_session, t, u, count=7)
        resp = client_auth_on.get(
            "/audit?limit=3",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 7
        assert len(body["items"]) == 3

    def test_list_audit_filter_by_event_type(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_audit_events(db_session, t, u, count=8)
        resp = client_auth_on.get(
            "/audit?event_type=job.enqueued",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["total"] == 2
        assert all(e["event_type"] == "job.enqueued" for e in body["items"])

    def test_list_audit_meta_is_sanitized(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_audit_events(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/audit",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        for item in body["items"]:
            assert isinstance(item.get("meta"), dict) or item.get("meta") is None

    def test_list_audit_tenant_isolation(self, client_auth_on, db_session):
        t1, u1 = _seed_tenant_and_user(db_session, email="a@t1.com", tenant_name="T1", role=Role.viewer)
        t2, u2 = _seed_tenant_and_user(db_session, email="b@t2.com", tenant_name="T2", role=Role.viewer)
        _seed_audit_events(db_session, t1, u1, count=5)
        _seed_audit_events(db_session, t2, u2, count=3)
        token_a = _make_token(u1)
        resp = client_auth_on.get(
            "/audit",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        body = resp.json()
        assert body["total"] == 5

    def test_list_audit_filter_by_since(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_audit_events(db_session, t, u, count=5)
        resp = client_auth_on.get(
            "/audit?since=2025-06-01T15:00:00%2B00:00",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["total"] == 2


# ===================================================================
# 4. Auth OFF → 503
# ===================================================================


class TestAuthOff503:
    def test_get_jobs_returns_503(self, client_auth_off):
        resp = client_auth_off.get("/jobs")
        assert resp.status_code == 503
        assert "auth" in resp.json()["detail"].lower()

    def test_get_batches_returns_503(self, client_auth_off):
        resp = client_auth_off.get("/apply-prices/batches")
        assert resp.status_code == 503
        assert "auth" in resp.json()["detail"].lower()

    def test_get_audit_returns_503(self, client_auth_off):
        resp = client_auth_off.get("/audit")
        assert resp.status_code == 503
        assert "auth" in resp.json()["detail"].lower()


# ===================================================================
# 5. Viewer role can access list endpoints
# ===================================================================


class TestViewerAccess:
    def test_viewer_can_list_jobs(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_viewer_can_list_batches(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/apply-prices/batches",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_viewer_can_list_audit(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/audit",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200


# ===================================================================
# 6. Response shape validation
# ===================================================================


class TestResponseShape:
    def test_job_list_shape(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_jobs(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert "total" in body
        assert "items" in body
        item = body["items"][0]
        expected_keys = {"id", "status", "created_at", "started_at", "finished_at", "user_id", "error"}
        assert expected_keys == set(item.keys())

    def test_batch_list_shape(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_batches(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/apply-prices/batches",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert "total" in body
        assert "items" in body
        item = body["items"][0]
        expected_keys = {"id", "mode", "status", "created_at", "finished_at", "user_id"}
        assert expected_keys == set(item.keys())

    def test_audit_list_shape(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        _seed_audit_events(db_session, t, u, count=1)
        resp = client_auth_on.get(
            "/audit",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert "total" in body
        assert "items" in body
        item = body["items"][0]
        expected_keys = {"id", "event_type", "created_at", "user_id", "meta"}
        assert expected_keys == set(item.keys())

    def test_empty_list_returns_zero_total(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)
        resp = client_auth_on.get(
            "/jobs",
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []
