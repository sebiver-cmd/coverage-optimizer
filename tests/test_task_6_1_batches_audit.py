"""Tests for Task 6.1 — Tenant-scoped batches + audit trail.

Covers:
1. Models: OptimizationJob, ApplyBatch, AuditEvent created in SQLite.
2. Repositories: jobs_repo and batches_repo CRUD + tenant isolation.
3. Endpoint integration (auth ON): DB rows created by jobs/apply endpoints.
4. Endpoint integration (auth OFF): legacy behavior preserved (no DB required).
5. RBAC / tenant isolation: user from tenant A cannot see tenant B records.

All tests use an in-memory SQLite database (no Postgres/Redis required).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
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
from backend.repositories import batches_repo, jobs_repo

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
    redis_url: str | None = None,
):
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false")
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
        db_session, monkeypatch, auth_required=True, redis_url="redis://localhost:6379/0"
    )


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    yield from _make_client(db_session, monkeypatch, auth_required=False)


# ===================================================================
# 1. Model / table creation tests
# ===================================================================


class TestModelsExist:
    """Verify that the new models create tables in SQLite."""

    def test_optimization_job_table(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        job = OptimizationJob(
            id=uuid.uuid4(),
            tenant_id=t.id,
            user_id=u.id,
            status="queued",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(job)
        db_session.commit()
        assert db_session.query(OptimizationJob).count() == 1

    def test_apply_batch_table(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        batch = ApplyBatch(
            id=uuid.uuid4(),
            tenant_id=t.id,
            user_id=u.id,
            mode="dry_run",
            status="created",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(batch)
        db_session.commit()
        assert db_session.query(ApplyBatch).count() == 1

    def test_audit_event_table(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        evt = AuditEvent(
            id=uuid.uuid4(),
            tenant_id=t.id,
            user_id=u.id,
            event_type="job.enqueued",
            created_at=datetime.now(timezone.utc),
            meta_json=json.dumps({"foo": "bar"}),
        )
        db_session.add(evt)
        db_session.commit()
        assert db_session.query(AuditEvent).count() == 1


# ===================================================================
# 2. Repository tests
# ===================================================================


class TestJobsRepo:
    def test_create_and_get_job(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(
            db_session,
            tenant_id=t.id,
            user_id=u.id,
            request_meta={"filter": "test"},
        )
        assert job.status == "queued"
        assert job.tenant_id == t.id
        fetched = jobs_repo.get_job(db_session, job_id=job.id, tenant_id=t.id)
        assert fetched is not None
        assert fetched.id == job.id

    def test_update_job_status_running(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)
        now = datetime.now(timezone.utc)
        updated = jobs_repo.update_job_status(
            db_session,
            job_id=job.id,
            tenant_id=t.id,
            status="running",
            started_at=now,
        )
        assert updated.status == "running"
        assert updated.started_at is not None

    def test_update_job_status_completed(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)
        now = datetime.now(timezone.utc)
        updated = jobs_repo.update_job_status(
            db_session,
            job_id=job.id,
            tenant_id=t.id,
            status="completed",
            finished_at=now,
            result={"summary": {}, "rows": []},
        )
        assert updated.status == "completed"
        assert updated.result_json is not None
        data = json.loads(updated.result_json)
        assert data["summary"] == {}

    def test_update_job_status_failed(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)
        updated = jobs_repo.update_job_status(
            db_session,
            job_id=job.id,
            tenant_id=t.id,
            status="failed",
            finished_at=datetime.now(timezone.utc),
            error="Connection refused",
        )
        assert updated.status == "failed"
        assert updated.error == "Connection refused"

    def test_list_jobs(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)
        jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)
        results = jobs_repo.list_jobs(db_session, tenant_id=t.id)
        assert len(results) == 2

    def test_tenant_isolation(self, db_session):
        """Job from tenant A is not visible to tenant B."""
        t1, u1 = _seed_tenant_and_user(
            db_session, email="a@t1.com", tenant_name="T1"
        )
        t2, _u2 = _seed_tenant_and_user(
            db_session, email="b@t2.com", tenant_name="T2"
        )
        job = jobs_repo.create_job(db_session, tenant_id=t1.id, user_id=u1.id)
        # Tenant B cannot see it
        fetched = jobs_repo.get_job(db_session, job_id=job.id, tenant_id=t2.id)
        assert fetched is None

    def test_emit_job_audit(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        jobs_repo.emit_job_audit(
            db_session,
            tenant_id=t.id,
            user_id=u.id,
            event_type="job.enqueued",
            meta={"job_id": "test"},
        )
        assert db_session.query(AuditEvent).count() == 1
        evt = db_session.query(AuditEvent).first()
        assert evt.event_type == "job.enqueued"


class TestBatchesRepo:
    def test_create_and_get_batch(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        batch = batches_repo.create_batch(
            db_session,
            tenant_id=t.id,
            user_id=u.id,
            mode="dry_run",
            manifest_meta={"changes": []},
        )
        assert batch.status == "created"
        assert batch.mode == "dry_run"
        fetched = batches_repo.get_batch(db_session, batch_id=batch.id, tenant_id=t.id)
        assert fetched is not None
        assert fetched.id == batch.id

    def test_update_batch_status(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        batch = batches_repo.create_batch(
            db_session, tenant_id=t.id, user_id=u.id, mode="apply"
        )
        now = datetime.now(timezone.utc)
        updated = batches_repo.update_batch_status(
            db_session,
            batch_id=batch.id,
            tenant_id=t.id,
            status="completed",
            finished_at=now,
            summary={"total_rows": 10, "applied_count": 8},
        )
        assert updated.status == "completed"
        assert json.loads(updated.summary_json)["applied_count"] == 8

    def test_list_batches(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        batches_repo.create_batch(db_session, tenant_id=t.id, user_id=u.id, mode="dry_run")
        batches_repo.create_batch(db_session, tenant_id=t.id, user_id=u.id, mode="apply")
        results = batches_repo.list_batches(db_session, tenant_id=t.id)
        assert len(results) == 2

    def test_tenant_isolation(self, db_session):
        """Batch from tenant A is not visible to tenant B."""
        t1, u1 = _seed_tenant_and_user(
            db_session, email="a@t1.com", tenant_name="T1"
        )
        t2, _u2 = _seed_tenant_and_user(
            db_session, email="b@t2.com", tenant_name="T2"
        )
        batch = batches_repo.create_batch(
            db_session, tenant_id=t1.id, user_id=u1.id, mode="dry_run"
        )
        fetched = batches_repo.get_batch(db_session, batch_id=batch.id, tenant_id=t2.id)
        assert fetched is None

    def test_emit_batch_audit(self, db_session):
        t, u = _seed_tenant_and_user(db_session)
        batches_repo.emit_batch_audit(
            db_session,
            tenant_id=t.id,
            user_id=u.id,
            event_type="apply.dry_run.completed",
            meta={"batch_id": "test"},
        )
        assert db_session.query(AuditEvent).count() == 1


# ===================================================================
# 3. Endpoint integration: auth ON — DB rows created
# ===================================================================


class TestJobsEndpointAuthOn:
    """POST /jobs/optimize creates DB row when auth required."""

    @pytest.mark.anyio
    async def test_enqueue_creates_db_job(self, client_auth_on, db_session):
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
        body = resp.json()
        job_id = body["job_id"]

        # Verify DB row was created
        row = db_session.query(OptimizationJob).filter(
            OptimizationJob.id == uuid.UUID(job_id)
        ).first()
        assert row is not None
        assert row.status == "queued"
        assert row.tenant_id == t.id
        assert row.user_id == u.id

        # Verify audit event was created
        audit = db_session.query(AuditEvent).filter(
            AuditEvent.event_type == "job.enqueued"
        ).first()
        assert audit is not None
        assert audit.tenant_id == t.id

    @pytest.mark.anyio
    async def test_get_job_status_from_db(self, client_auth_on, db_session):
        """GET /jobs/{id} reads from DB when auth required."""
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)

        # Create a job directly in DB
        job = jobs_repo.create_job(
            db_session, tenant_id=t.id, user_id=u.id,
            request_meta={"test": True},
        )
        jobs_repo.update_job_status(
            db_session,
            job_id=job.id,
            tenant_id=t.id,
            status="completed",
            finished_at=datetime.now(timezone.utc),
            result={"summary": {}, "rows": []},
        )

        resp = client_auth_on.get(
            f"/jobs/{job.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["result"] == {"summary": {}, "rows": []}

    @pytest.mark.anyio
    async def test_get_job_tenant_isolation(self, client_auth_on, db_session):
        """User from tenant B cannot see tenant A's job (404)."""
        t1, u1 = _seed_tenant_and_user(
            db_session, email="a@t1.com", tenant_name="T1", role=Role.operator
        )
        t2, u2 = _seed_tenant_and_user(
            db_session, email="b@t2.com", tenant_name="T2", role=Role.operator
        )

        job = jobs_repo.create_job(db_session, tenant_id=t1.id, user_id=u1.id)
        token_b = _make_token(u2)

        resp = client_auth_on.get(
            f"/jobs/{job.id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404


# ===================================================================
# 4. Endpoint integration: auth OFF — legacy behavior preserved
# ===================================================================


class TestJobsEndpointAuthOff:
    """When auth is off, endpoints still work without DB."""

    @pytest.mark.anyio
    async def test_enqueue_without_db(self, client_auth_off, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        get_settings.cache_clear()

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
            resp = client_auth_off.post(
                "/jobs/optimize",
                json={
                    "api_username": "test@example.com",
                    "api_password": "pw",
                },
            )

        assert resp.status_code == 200
        assert "job_id" in resp.json()

    def test_no_redis_returns_503(self, client_auth_off, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        get_settings.cache_clear()
        resp = client_auth_off.post(
            "/jobs/optimize",
            json={
                "api_username": "test@example.com",
                "api_password": "pw",
            },
        )
        assert resp.status_code == 503


# ===================================================================
# 5. Apply batch endpoints: auth ON — DB rows created
# ===================================================================


class TestApplyBatchEndpointAuthOn:
    """Dry-run and create-manifest create DB batch records when auth ON."""

    def test_dry_run_creates_db_batch(self, client_auth_on, db_session, monkeypatch):
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)

        # Mock the optimization function to return a minimal result
        from backend.optimizer_api import OptimizeResponse, OptimizeSummary, ProductRow

        mock_result = OptimizeResponse(
            rows=[
                ProductRow(
                    product_id="1",
                    item_number="SKU-1",
                    title="Widget",
                    buy_price=50.0,
                    current_price=100.0,
                    current_price_ex_vat=80.0,
                    current_coverage_pct=37.5,
                    suggested_price=110.0,
                    suggested_price_ex_vat=88.0,
                    suggested_coverage_pct=43.2,
                    needs_adjustment=True,
                )
            ],
            summary=OptimizeSummary(
                total_products=1,
                base_products=1,
                total_rows=1,
                adjusted_count=1,
                unchanged_count=0,
                adjusted_pct=100.0,
                avg_current_coverage_pct=37.5,
                avg_suggested_coverage_pct=43.2,
            ),
        )

        with patch("backend.apply_prices_api.run_optimization", return_value=mock_result):
            resp = client_auth_on.post(
                "/apply-prices/dry-run",
                json={
                    "optimize_payload": {
                        "api_username": "test@example.com",
                        "api_password": "pw",
                    }
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        batch_id = resp.json()["batch_id"]

        # Verify DB row was created
        row = db_session.query(ApplyBatch).filter(
            ApplyBatch.id == uuid.UUID(batch_id)
        ).first()
        assert row is not None
        assert row.mode == "dry_run"
        assert row.status == "completed"
        assert row.tenant_id == t.id

    def test_create_manifest_creates_db_batch(self, client_auth_on, db_session):
        t, u = _seed_tenant_and_user(db_session, role=Role.operator)
        token = _make_token(u)

        resp = client_auth_on.post(
            "/apply-prices/create-manifest",
            json={
                "changes": [
                    {
                        "NUMBER": "SKU-1",
                        "TITLE_DK": "Widget",
                        "old_price": 100.0,
                        "new_price": 110.0,
                        "change_pct": 10.0,
                    }
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        batch_id = resp.json()["batch_id"]

        row = db_session.query(ApplyBatch).filter(
            ApplyBatch.id == uuid.UUID(batch_id)
        ).first()
        assert row is not None
        assert row.mode == "create_manifest"
        assert row.status == "completed"
        assert row.tenant_id == t.id

    def test_get_batch_reads_from_db(self, client_auth_on, db_session):
        """GET /apply-prices/batch/{id} reads from DB when auth ON."""
        t, u = _seed_tenant_and_user(db_session, role=Role.viewer)
        token = _make_token(u)

        bid = uuid.uuid4()
        batches_repo.create_batch(
            db_session,
            batch_id=bid,
            tenant_id=t.id,
            user_id=u.id,
            mode="dry_run",
            manifest_meta={"batch_id": str(bid), "changes": [{"NUMBER": "A"}]},
        )
        batches_repo.update_batch_status(
            db_session,
            batch_id=bid,
            tenant_id=t.id,
            status="completed",
            summary={"total": 1},
        )

        resp = client_auth_on.get(
            f"/apply-prices/batch/{bid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["batch_id"] == str(bid)
        assert body["status"] == "completed"
        assert body["mode"] == "dry_run"

    def test_get_batch_tenant_isolation(self, client_auth_on, db_session):
        """User from tenant B cannot see tenant A's batch (404)."""
        t1, u1 = _seed_tenant_and_user(
            db_session, email="a@t1.com", tenant_name="T1", role=Role.viewer
        )
        t2, u2 = _seed_tenant_and_user(
            db_session, email="b@t2.com", tenant_name="T2", role=Role.viewer
        )

        bid = uuid.uuid4()
        batches_repo.create_batch(
            db_session,
            batch_id=bid,
            tenant_id=t1.id,
            user_id=u1.id,
            mode="dry_run",
        )

        token_b = _make_token(u2)
        resp = client_auth_on.get(
            f"/apply-prices/batch/{bid}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404


# ===================================================================
# 6. Apply batch endpoints: auth OFF — legacy behavior preserved
# ===================================================================


class TestApplyBatchEndpointAuthOff:
    """When auth off, dry-run still works via disk manifests."""

    def test_dry_run_without_auth(self, client_auth_off, monkeypatch):
        from backend.optimizer_api import OptimizeResponse, OptimizeSummary, ProductRow

        mock_result = OptimizeResponse(
            rows=[
                ProductRow(
                    product_id="1",
                    item_number="SKU-1",
                    title="Widget",
                    buy_price=50.0,
                    current_price=100.0,
                    current_price_ex_vat=80.0,
                    current_coverage_pct=37.5,
                    suggested_price=110.0,
                    suggested_price_ex_vat=88.0,
                    suggested_coverage_pct=43.2,
                    needs_adjustment=True,
                )
            ],
            summary=OptimizeSummary(
                total_products=1,
                base_products=1,
                total_rows=1,
                adjusted_count=1,
                unchanged_count=0,
                adjusted_pct=100.0,
                avg_current_coverage_pct=37.5,
                avg_suggested_coverage_pct=43.2,
            ),
        )

        with patch("backend.apply_prices_api.run_optimization", return_value=mock_result):
            resp = client_auth_off.post(
                "/apply-prices/dry-run",
                json={
                    "optimize_payload": {
                        "api_username": "test@example.com",
                        "api_password": "pw",
                    }
                },
            )

        assert resp.status_code == 200
        assert "batch_id" in resp.json()

    def test_create_manifest_without_auth(self, client_auth_off):
        resp = client_auth_off.post(
            "/apply-prices/create-manifest",
            json={
                "changes": [
                    {
                        "NUMBER": "SKU-1",
                        "TITLE_DK": "Widget",
                        "old_price": 100.0,
                        "new_price": 110.0,
                        "change_pct": 10.0,
                    }
                ]
            },
        )
        assert resp.status_code == 200
        assert "batch_id" in resp.json()


# ===================================================================
# 7. Worker DB update function
# ===================================================================


class TestWorkerDbUpdate:
    """Test that _update_job_in_db writes to the DB correctly."""

    def test_update_running(self, db_session, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()

        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)

        from backend.worker import _update_job_in_db

        def mock_get_db():
            try:
                yield db_session
            finally:
                pass

        with patch("backend.db.get_db", mock_get_db), \
             patch("backend.db._SessionLocal", True):  # make get_db not raise
            _update_job_in_db(str(job.id), "running")

        db_session.refresh(job)
        assert job.status == "running"
        assert job.started_at is not None

    def test_update_completed_with_result(self, db_session, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()

        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)

        from backend.worker import _update_job_in_db

        def mock_get_db():
            try:
                yield db_session
            finally:
                pass

        with patch("backend.db.get_db", mock_get_db), \
             patch("backend.db._SessionLocal", True):
            _update_job_in_db(str(job.id), "completed", result={"ok": True})

        db_session.refresh(job)
        assert job.status == "completed"
        assert job.finished_at is not None
        assert json.loads(job.result_json) == {"ok": True}

    def test_noop_when_auth_disabled(self, db_session, monkeypatch):
        """When auth is off, _update_job_in_db is a no-op."""
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "false")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()

        t, u = _seed_tenant_and_user(db_session)
        job = jobs_repo.create_job(db_session, tenant_id=t.id, user_id=u.id)

        from backend.worker import _update_job_in_db

        _update_job_in_db(str(job.id), "running")

        db_session.refresh(job)
        # Should still be queued because the function is a no-op
        assert job.status == "queued"


# ===================================================================
# 8. Sanitization: no credentials stored in DB
# ===================================================================


class TestSanitization:
    """Ensure api_username/api_password are stripped from stored JSON."""

    @pytest.mark.anyio
    async def test_job_request_json_no_credentials(self, client_auth_on, db_session):
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
                    "api_username": "secret_user",
                    "api_password": "secret_pass",
                },
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        job_id = resp.json()["job_id"]

        row = db_session.query(OptimizationJob).filter(
            OptimizationJob.id == uuid.UUID(job_id)
        ).first()
        assert row is not None
        meta = json.loads(row.request_json)
        assert "api_password" not in meta
        assert "api_username" not in meta
