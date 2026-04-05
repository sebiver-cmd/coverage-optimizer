"""Tests for billing gate dependency (Task 7.1 completion).

Covers:
1. Tenants with inactive billing_status → 402 on paid endpoints.
2. Tenants with billing_status="active" → pass through (no 402).
3. BILLING_ENABLED=false → gate disabled, all pass.
4. SBOPTIMA_AUTH_REQUIRED=false → gate disabled, all pass.
5. Free-tier tenant with billing_status=None → 402.
6. Matrix of billing_status values × endpoints.

All tests use an in-memory SQLite database (no Postgres/Redis/Stripe required).
"""

from __future__ import annotations

import uuid
from typing import Generator

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
from backend.models import Role, Tenant, User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"
_FERNET_KEY = Fernet.generate_key().decode()
_STRIPE_SK = "sk_test_fake_key_for_billing_gate"

# Paid endpoints that must be gated.
_GATED_ENDPOINTS = [
    ("POST", "/jobs/optimize"),
    ("POST", "/apply-prices/apply"),
    ("POST", "/optimize/"),
]

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
    email: str = "admin@gate.test",
    tenant_name: str = "GateTenant",
    billing_status: str | None = None,
) -> tuple[Tenant, User]:
    """Create and return a (Tenant, User) pair."""
    t = Tenant(
        id=uuid.uuid4(),
        name=tenant_name,
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
        sub=user.id, tenant_id=user.tenant_id, role=user.role.value,
    )


def _make_client(
    db_session: Session,
    monkeypatch,
    *,
    auth_required: bool = True,
    billing_enabled: bool = True,
) -> Generator[TestClient, None, None]:
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv(
        "SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false",
    )
    monkeypatch.setenv("BILLING_ENABLED", "true" if billing_enabled else "false")
    # Disable features not needed by these tests.
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("SB_OPTIMA_ENABLE_APPLY", raising=False)
    # Stripe key required for the billing gate to activate.
    monkeypatch.setenv("STRIPE_SECRET_KEY", _STRIPE_SK)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID_PRO", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID_ENTERPRISE", raising=False)
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


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(client: TestClient, path: str, token: str) -> int:
    """POST to *path* with auth header and minimal JSON body, return status code."""
    # Provide a minimal JSON body that satisfies pydantic if the request
    # actually reaches the endpoint (it usually won't for 402 tests).
    return client.post(
        path,
        json={},
        headers=_auth_header(token),
    ).status_code


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_402_DETAIL = "Payment required \u2014 please update your subscription."


# ===========================================================================
# 1. Inactive billing_status → 402 on every gated endpoint
# ===========================================================================


class TestInactiveBillingReturns402:
    """Tenants with billing_status NOT 'active' must receive 402."""

    @pytest.mark.parametrize(
        "billing_status",
        [None, "canceled", "past_due", "inactive"],
        ids=["null", "canceled", "past_due", "inactive"],
    )
    @pytest.mark.parametrize(
        "method_path",
        _GATED_ENDPOINTS,
        ids=[f"{m} {p}" for m, p in _GATED_ENDPOINTS],
    )
    def test_402_for_non_active_status(
        self,
        db_session,
        monkeypatch,
        billing_status,
        method_path,
    ):
        _method, path = method_path
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status=billing_status, role=Role.admin,
        )
        token = _make_token(user)
        gen = _make_client(db_session, monkeypatch, auth_required=True, billing_enabled=True)
        client = next(gen)
        try:
            resp = client.post(path, json={}, headers=_auth_header(token))
            assert resp.status_code == 402, (
                f"Expected 402 for billing_status={billing_status!r} on {path}, got {resp.status_code}"
            )
            assert resp.json()["detail"] == _402_DETAIL
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass


# ===========================================================================
# 2. Active tenant → passes through (no 402)
# ===========================================================================


class TestActiveTenantPassesThrough:
    """Tenant with billing_status='active' must NOT get 402.

    The endpoint may still fail for other reasons (503 Redis unavailable,
    403 apply disabled, 422 validation error, etc.) — that's fine; we only
    verify that the response is NOT 402.
    """

    @pytest.mark.parametrize(
        "method_path",
        _GATED_ENDPOINTS,
        ids=[f"{m} {p}" for m, p in _GATED_ENDPOINTS],
    )
    def test_active_no_402(
        self,
        db_session,
        monkeypatch,
        method_path,
    ):
        _method, path = method_path
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status="active", role=Role.admin,
        )
        token = _make_token(user)
        gen = _make_client(db_session, monkeypatch, auth_required=True, billing_enabled=True)
        client = next(gen)
        try:
            resp = client.post(path, json={}, headers=_auth_header(token))
            assert resp.status_code != 402, (
                f"Active tenant should not get 402 on {path}, got {resp.status_code}"
            )
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass


# ===========================================================================
# 3. BILLING_ENABLED=false → gate disabled, all pass (no 402)
# ===========================================================================


class TestBillingDisabledSkipsGate:
    """When BILLING_ENABLED=false, no endpoint should return 402."""

    @pytest.mark.parametrize(
        "billing_status",
        [None, "canceled", "past_due", "inactive"],
        ids=["null", "canceled", "past_due", "inactive"],
    )
    @pytest.mark.parametrize(
        "method_path",
        _GATED_ENDPOINTS,
        ids=[f"{m} {p}" for m, p in _GATED_ENDPOINTS],
    )
    def test_no_402_when_billing_disabled(
        self,
        db_session,
        monkeypatch,
        billing_status,
        method_path,
    ):
        _method, path = method_path
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status=billing_status, role=Role.admin,
        )
        token = _make_token(user)
        gen = _make_client(
            db_session, monkeypatch, auth_required=True, billing_enabled=False,
        )
        client = next(gen)
        try:
            resp = client.post(path, json={}, headers=_auth_header(token))
            assert resp.status_code != 402, (
                f"billing_enabled=false should never 402 on {path}, got {resp.status_code}"
            )
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass


# ===========================================================================
# 4. SBOPTIMA_AUTH_REQUIRED=false → gate disabled, all pass (no 402)
# ===========================================================================


class TestAuthOffSkipsGate:
    """When SBOPTIMA_AUTH_REQUIRED=false, no endpoint should return 402."""

    @pytest.mark.parametrize(
        "method_path",
        _GATED_ENDPOINTS,
        ids=[f"{m} {p}" for m, p in _GATED_ENDPOINTS],
    )
    def test_no_402_when_auth_off(
        self,
        db_session,
        monkeypatch,
        method_path,
    ):
        _method, path = method_path
        # Tenant has no billing (would be 402 if gate were active).
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status=None, role=Role.admin,
        )
        gen = _make_client(
            db_session, monkeypatch, auth_required=False, billing_enabled=True,
        )
        client = next(gen)
        try:
            # No auth header needed when auth is off.
            resp = client.post(path, json={})
            assert resp.status_code != 402, (
                f"auth_required=false should never 402 on {path}, got {resp.status_code}"
            )
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass


# ===========================================================================
# 5. Free-tier tenant (billing_status=None) → 402
# ===========================================================================


class TestFreeTierReturns402:
    """A tenant that has never subscribed (billing_status=None) gets 402."""

    def test_free_tier_402(self, db_session, monkeypatch):
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status=None, role=Role.admin,
        )
        token = _make_token(user)
        gen = _make_client(db_session, monkeypatch, auth_required=True, billing_enabled=True)
        client = next(gen)
        try:
            resp = client.post(
                "/jobs/optimize",
                json={},
                headers=_auth_header(token),
            )
            assert resp.status_code == 402
            assert resp.json()["detail"] == _402_DETAIL
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass


# ===========================================================================
# 6. Correct 402 response body
# ===========================================================================


class TestResponseBody:
    """The 402 response must include the exact expected detail message."""

    def test_detail_message(self, db_session, monkeypatch):
        tenant, user = _seed_tenant_and_user(
            db_session, billing_status="canceled", role=Role.admin,
        )
        token = _make_token(user)
        gen = _make_client(db_session, monkeypatch, auth_required=True, billing_enabled=True)
        client = next(gen)
        try:
            resp = client.post(
                "/optimize/",
                json={},
                headers=_auth_header(token),
            )
            assert resp.status_code == 402
            body = resp.json()
            assert body == {"detail": _402_DETAIL}
        finally:
            try:
                next(gen, None)
            except StopIteration:
                pass
