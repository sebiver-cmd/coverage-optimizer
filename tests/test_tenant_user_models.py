"""Tests for Task 4.1 — Tenant + User models and API (SQLite in-memory).

These tests exercise:
- SQLAlchemy model creation via a raw session.
- Unique constraint enforcement (tenant_id, email).
- Role enum persistence and loading.
- FastAPI CRUD endpoints via TestClient with a DB-session override.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db import Base, get_db
from backend.main import app
from backend.models import Role, Tenant, User

# ---------------------------------------------------------------------------
# SQLite in-memory engine + session fixture
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"


@pytest.fixture()
def db_session():
    """Yield a SQLAlchemy session backed by an in-memory SQLite database.

    Each test gets a fresh schema so there is no cross-test pollution.
    """
    engine = create_engine(
        _SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable foreign-key enforcement on SQLite.
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


@pytest.fixture()
def client(db_session: Session):
    """FastAPI TestClient with ``get_db`` overridden to use the SQLite session."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # session lifecycle managed by fixture

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ===========================================================================
# Model-level tests (raw session)
# ===========================================================================


class TestTenantModel:
    """Direct ORM tests for the Tenant model."""

    def test_create_tenant(self, db_session: Session):
        t = Tenant(name="Acme Corp")
        db_session.add(t)
        db_session.commit()
        db_session.refresh(t)

        assert isinstance(t.id, uuid.UUID)
        assert t.name == "Acme Corp"
        assert t.created_at is not None

    def test_tenant_optional_fields(self, db_session: Session):
        t = Tenant(name="Widgets Inc", plan="pro", status="active", stripe_customer_id="cus_123")
        db_session.add(t)
        db_session.commit()
        db_session.refresh(t)

        assert t.plan == "pro"
        assert t.status == "active"
        assert t.stripe_customer_id == "cus_123"


class TestUserModel:
    """Direct ORM tests for the User model."""

    def _make_tenant(self, db_session: Session, name: str = "T") -> Tenant:
        t = Tenant(name=name)
        db_session.add(t)
        db_session.commit()
        db_session.refresh(t)
        return t

    def test_create_user_default_role(self, db_session: Session):
        t = self._make_tenant(db_session)
        u = User(tenant_id=t.id, email="a@b.com", password_hash="hashed")
        db_session.add(u)
        db_session.commit()
        db_session.refresh(u)

        assert isinstance(u.id, uuid.UUID)
        assert u.tenant_id == t.id
        assert u.email == "a@b.com"
        assert u.role == Role.operator  # default

    def test_create_user_explicit_role(self, db_session: Session):
        t = self._make_tenant(db_session)
        u = User(tenant_id=t.id, email="admin@x.com", password_hash="h", role=Role.admin)
        db_session.add(u)
        db_session.commit()
        db_session.refresh(u)

        assert u.role == Role.admin

    def test_role_enum_roundtrip(self, db_session: Session):
        """All four role values persist and reload correctly."""
        t = self._make_tenant(db_session)
        for i, role in enumerate(Role):
            u = User(
                tenant_id=t.id,
                email=f"user{i}@test.com",
                password_hash="h",
                role=role,
            )
            db_session.add(u)
        db_session.commit()

        users = db_session.query(User).filter(User.tenant_id == t.id).all()
        persisted_roles = {u.role for u in users}
        assert persisted_roles == {Role.owner, Role.admin, Role.operator, Role.viewer}

    def test_unique_constraint_same_tenant_same_email(self, db_session: Session):
        t = self._make_tenant(db_session)
        u1 = User(tenant_id=t.id, email="dup@test.com", password_hash="h")
        db_session.add(u1)
        db_session.commit()

        u2 = User(tenant_id=t.id, email="dup@test.com", password_hash="h2")
        db_session.add(u2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()
        db_session.rollback()

    def test_unique_constraint_different_tenant_same_email(self, db_session: Session):
        """Same email under *different* tenants is allowed."""
        t1 = self._make_tenant(db_session, "T1")
        t2 = self._make_tenant(db_session, "T2")
        u1 = User(tenant_id=t1.id, email="shared@test.com", password_hash="h")
        u2 = User(tenant_id=t2.id, email="shared@test.com", password_hash="h")
        db_session.add_all([u1, u2])
        db_session.commit()  # should not raise

        assert u1.id != u2.id

    def test_cascade_delete(self, db_session: Session):
        """Deleting a tenant cascades to its users."""
        t = self._make_tenant(db_session)
        u = User(tenant_id=t.id, email="gone@test.com", password_hash="h")
        db_session.add(u)
        db_session.commit()

        db_session.delete(t)
        db_session.commit()

        assert db_session.query(User).count() == 0

    def test_tenant_relationship(self, db_session: Session):
        """The user.tenant relationship resolves correctly."""
        t = self._make_tenant(db_session, "RelTest")
        u = User(tenant_id=t.id, email="rel@test.com", password_hash="h")
        db_session.add(u)
        db_session.commit()
        db_session.refresh(u)

        assert u.tenant.name == "RelTest"
        assert u in t.users


# ===========================================================================
# API-level tests (FastAPI TestClient)
# ===========================================================================


class TestTenantAPI:
    """Test the tenant CRUD endpoints via the FastAPI TestClient."""

    def test_create_tenant(self, client: TestClient):
        resp = client.post("/tenants", json={"name": "API Tenant", "plan": "free"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "API Tenant"
        assert data["plan"] == "free"
        assert "id" in data

    def test_get_tenant(self, client: TestClient):
        resp = client.post("/tenants", json={"name": "Fetch Me"})
        tenant_id = resp.json()["id"]

        resp = client.get(f"/tenants/{tenant_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Fetch Me"

    def test_get_tenant_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/tenants/{fake_id}")
        assert resp.status_code == 404

    def test_create_tenant_empty_name(self, client: TestClient):
        resp = client.post("/tenants", json={"name": ""})
        assert resp.status_code == 422


class TestUserAPI:
    """Test the user CRUD endpoints via the FastAPI TestClient."""

    def _create_tenant(self, client: TestClient, name: str = "T") -> str:
        resp = client.post("/tenants", json={"name": name})
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_create_user(self, client: TestClient):
        tid = self._create_tenant(client)
        resp = client.post(
            f"/tenants/{tid}/users",
            json={"email": "u@x.com", "password_hash": "hashed123"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "u@x.com"
        assert data["role"] == "operator"
        assert "password_hash" not in data  # never exposed

    def test_create_user_with_role(self, client: TestClient):
        tid = self._create_tenant(client)
        resp = client.post(
            f"/tenants/{tid}/users",
            json={"email": "admin@x.com", "password_hash": "h", "role": "admin"},
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "admin"

    def test_list_users(self, client: TestClient):
        tid = self._create_tenant(client)
        client.post(f"/tenants/{tid}/users", json={"email": "a@x.com", "password_hash": "h"})
        client.post(f"/tenants/{tid}/users", json={"email": "b@x.com", "password_hash": "h"})

        resp = client.get(f"/tenants/{tid}/users")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_duplicate_email_same_tenant_409(self, client: TestClient):
        tid = self._create_tenant(client)
        client.post(f"/tenants/{tid}/users", json={"email": "dup@x.com", "password_hash": "h"})
        resp = client.post(
            f"/tenants/{tid}/users",
            json={"email": "dup@x.com", "password_hash": "h2"},
        )
        assert resp.status_code == 409

    def test_duplicate_email_different_tenant_ok(self, client: TestClient):
        tid1 = self._create_tenant(client, "T1")
        tid2 = self._create_tenant(client, "T2")
        r1 = client.post(f"/tenants/{tid1}/users", json={"email": "same@x.com", "password_hash": "h"})
        r2 = client.post(f"/tenants/{tid2}/users", json={"email": "same@x.com", "password_hash": "h"})
        assert r1.status_code == 201
        assert r2.status_code == 201

    def test_create_user_tenant_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/tenants/{fake_id}/users",
            json={"email": "x@x.com", "password_hash": "h"},
        )
        assert resp.status_code == 404

    def test_list_users_tenant_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/tenants/{fake_id}/users")
        assert resp.status_code == 404
