"""Tests for Task 4.3 — RBAC middleware (role-gated dependencies on all routes).

Verifies that:
- When ``SBOPTIMA_AUTH_REQUIRED=true``, every endpoint enforces
  role-based access control per the mapping in ``SAAS_ROADMAP.md``.
- When ``SBOPTIMA_AUTH_REQUIRED=false``, no 401/403 is raised (legacy mode).

All tests use an in-memory SQLite database (no Postgres required).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import create_access_token, hash_password
from backend.config import get_settings
from backend.db import Base, get_db
from backend.main import app
from backend.models import Role, Tenant, User
from backend.rbac import ROLE_ORDER, _role_level

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"
_PASSWORD = "Str0ngP@ss!"

ALL_ROLES = ["viewer", "operator", "admin", "owner"]

# Endpoints grouped by minimum required role.
# Each entry: (method, path, min_role)
# For POST endpoints that need a body, we send an empty or minimal body — the
# auth layer should reject *before* validation when role is insufficient.
VIEWER_ENDPOINTS = [
    ("GET", "/health"),
    ("GET", "/brands?api_username=x&api_password=y"),
    ("POST", "/optimize"),
    ("POST", "/catalog/products"),
    ("GET", "/apply-prices/batch/00000000-0000-4000-a000-000000000000"),
    ("GET", "/apply-prices/status"),
]

OPERATOR_ENDPOINTS = [
    ("POST", "/apply-prices/dry-run"),
    ("POST", "/apply-prices/create-manifest"),
    ("POST", "/jobs/optimize"),
    ("GET", "/jobs/00000000-0000-4000-a000-000000000001"),
]

ADMIN_ENDPOINTS = [
    ("POST", "/apply-prices/apply"),
    ("POST", "/tenants"),
    ("GET", "/tenants/00000000-0000-4000-a000-000000000002"),
    ("POST", "/tenants/00000000-0000-4000-a000-000000000002/users"),
    ("GET", "/tenants/00000000-0000-4000-a000-000000000002/users"),
]

# Auth endpoints: /auth/me requires viewer when auth is enabled.
AUTH_ME_ENDPOINT = ("GET", "/auth/me")

# Public auth endpoints (no RBAC).
PUBLIC_AUTH_ENDPOINTS = [
    ("POST", "/auth/signup"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
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


def _make_client(db_session: Session, monkeypatch, *, auth_required: bool):
    """Build a TestClient with the given auth_required setting."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv(
        "SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false"
    )
    get_settings.cache_clear()

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    return client


@pytest.fixture()
def client_auth_required(db_session, monkeypatch):
    """TestClient with SBOPTIMA_AUTH_REQUIRED=true."""
    client = _make_client(db_session, monkeypatch, auth_required=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    """TestClient with SBOPTIMA_AUTH_REQUIRED=false."""
    client = _make_client(db_session, monkeypatch, auth_required=False)
    yield client
    app.dependency_overrides.clear()


def _seed_user(db: Session, role: str) -> User:
    """Create a tenant + user with the given role, return the User."""
    tenant = Tenant(name=f"Tenant-{role}")
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=f"{role}@test.com",
        password_hash=hash_password(_PASSWORD),
        role=Role(role),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token_for(user: User) -> str:
    """Generate a JWT for the given user."""
    return create_access_token(
        sub=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value,
    )


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(client: TestClient, method: str, path: str, headers: dict | None = None):
    """Issue a request, sending an empty JSON body for POST endpoints."""
    if method == "GET":
        return client.get(path, headers=headers)
    elif method == "POST":
        return client.post(path, json={}, headers=headers)
    raise ValueError(f"Unsupported method: {method}")


# ===========================================================================
# Unit tests — ROLE_ORDER / _role_level
# ===========================================================================


class TestRoleOrder:
    def test_ordering_is_correct(self):
        assert ROLE_ORDER[Role.viewer] < ROLE_ORDER[Role.operator]
        assert ROLE_ORDER[Role.operator] < ROLE_ORDER[Role.admin]
        assert ROLE_ORDER[Role.admin] < ROLE_ORDER[Role.owner]

    @pytest.mark.parametrize("role_str", ALL_ROLES)
    def test_role_level_accepts_strings(self, role_str):
        assert _role_level(role_str) == ROLE_ORDER[Role(role_str)]

    @pytest.mark.parametrize("role", list(Role))
    def test_role_level_accepts_enum(self, role):
        assert _role_level(role) == ROLE_ORDER[role]


# ===========================================================================
# Auth-required mode: viewer endpoints — accessible by all roles (viewer+)
# ===========================================================================


@pytest.mark.parametrize("method,path", VIEWER_ENDPOINTS)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_viewer_endpoints_allow_all_roles(
    db_session, client_auth_required, method, path, role
):
    """Viewer-level endpoints must return 200 (or non-401/403) for all roles."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = _request(client_auth_required, method, path, _auth_header(token))
    # Auth should pass — status should NOT be 401 or 403.
    assert resp.status_code not in (401, 403), (
        f"{method} {path} returned {resp.status_code} for role={role}"
    )


@pytest.mark.parametrize("method,path", VIEWER_ENDPOINTS)
def test_viewer_endpoints_reject_unauthenticated(
    client_auth_required, method, path
):
    """Viewer-level endpoints must return 401 when no token is supplied."""
    resp = _request(client_auth_required, method, path)
    assert resp.status_code == 401, (
        f"{method} {path} should be 401 without token, got {resp.status_code}"
    )


# ===========================================================================
# Auth-required mode: operator endpoints — viewer is denied, operator+ allowed
# ===========================================================================


@pytest.mark.parametrize("method,path", OPERATOR_ENDPOINTS)
def test_operator_endpoints_deny_viewer(
    db_session, client_auth_required, method, path
):
    """Operator-level endpoints must return 403 for viewers."""
    user = _seed_user(db_session, "viewer")
    token = _token_for(user)
    resp = _request(client_auth_required, method, path, _auth_header(token))
    assert resp.status_code == 403, (
        f"{method} {path} should be 403 for viewer, got {resp.status_code}"
    )


@pytest.mark.parametrize("method,path", OPERATOR_ENDPOINTS)
@pytest.mark.parametrize("role", ["operator", "admin", "owner"])
def test_operator_endpoints_allow_operator_and_above(
    db_session, client_auth_required, method, path, role
):
    """Operator-level endpoints must allow operator/admin/owner."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = _request(client_auth_required, method, path, _auth_header(token))
    assert resp.status_code not in (401, 403), (
        f"{method} {path} returned {resp.status_code} for role={role}"
    )


@pytest.mark.parametrize("method,path", OPERATOR_ENDPOINTS)
def test_operator_endpoints_reject_unauthenticated(
    client_auth_required, method, path
):
    """Operator-level endpoints must return 401 without a token."""
    resp = _request(client_auth_required, method, path)
    assert resp.status_code == 401


# ===========================================================================
# Auth-required mode: admin endpoints — viewer/operator denied, admin+ allowed
# ===========================================================================


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_admin_endpoints_deny_viewer_operator(
    db_session, client_auth_required, method, path, role
):
    """Admin-level endpoints must return 403 for viewer and operator."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = _request(client_auth_required, method, path, _auth_header(token))
    assert resp.status_code == 403, (
        f"{method} {path} should be 403 for {role}, got {resp.status_code}"
    )


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_endpoints_allow_admin_owner(
    db_session, client_auth_required, method, path, role
):
    """Admin-level endpoints must allow admin and owner."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = _request(client_auth_required, method, path, _auth_header(token))
    assert resp.status_code not in (401, 403), (
        f"{method} {path} returned {resp.status_code} for role={role}"
    )


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
def test_admin_endpoints_reject_unauthenticated(
    client_auth_required, method, path
):
    """Admin-level endpoints must return 401 without a token."""
    resp = _request(client_auth_required, method, path)
    assert resp.status_code == 401


# ===========================================================================
# Auth-required mode: /auth/me — requires viewer (any valid token)
# ===========================================================================


@pytest.mark.parametrize("role", ALL_ROLES)
def test_auth_me_allows_all_roles(
    db_session, client_auth_required, role
):
    """GET /auth/me should succeed for any authenticated user (viewer+)."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = client_auth_required.get("/auth/me", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == role


def test_auth_me_rejects_unauthenticated(client_auth_required):
    """GET /auth/me must return 401 without a token."""
    resp = client_auth_required.get("/auth/me")
    assert resp.status_code == 401


# ===========================================================================
# Auth-required mode: public auth endpoints remain accessible
# ===========================================================================


def test_auth_signup_is_public(client_auth_required):
    """POST /auth/signup should not require a token."""
    resp = client_auth_required.post(
        "/auth/signup",
        json={
            "tenant_name": "RBAC Test Org",
            "email": "rbac@test.com",
            "password": _PASSWORD,
        },
    )
    assert resp.status_code == 201


def test_auth_login_is_public(db_session, client_auth_required):
    """POST /auth/login should not require a token."""
    _seed_user(db_session, "operator")
    resp = client_auth_required.post(
        "/auth/login",
        json={"email": "operator@test.com", "password": _PASSWORD},
    )
    assert resp.status_code == 200


def test_auth_refresh_requires_valid_token(db_session, client_auth_required):
    """POST /auth/refresh requires a valid token (not RBAC-gated beyond that)."""
    user = _seed_user(db_session, "viewer")
    token = _token_for(user)
    resp = client_auth_required.post(
        "/auth/refresh",
        headers=_auth_header(token),
    )
    assert resp.status_code == 200


# ===========================================================================
# Auth-off mode: all endpoints bypass auth (no 401/403)
# ===========================================================================

_ALL_RBAC_ENDPOINTS = VIEWER_ENDPOINTS + OPERATOR_ENDPOINTS + ADMIN_ENDPOINTS


@pytest.mark.parametrize("method,path", _ALL_RBAC_ENDPOINTS)
def test_auth_off_no_401_403(client_auth_off, method, path):
    """When SBOPTIMA_AUTH_REQUIRED=false, no endpoint should return 401 or 403
    due to auth.  (422 for missing body is expected and acceptable.)
    """
    resp = _request(client_auth_off, method, path)
    assert resp.status_code not in (401, 403), (
        f"{method} {path} returned {resp.status_code} with auth off"
    )


def test_auth_off_health(client_auth_off):
    """GET /health should return 200 with auth off."""
    resp = client_auth_off.get("/health")
    assert resp.status_code == 200


def test_auth_off_apply_status(client_auth_off):
    """GET /apply-prices/status should return 200 with auth off."""
    resp = client_auth_off.get("/apply-prices/status")
    assert resp.status_code == 200


# ===========================================================================
# Edge cases
# ===========================================================================


def test_invalid_token_returns_401(client_auth_required):
    """A garbage token must produce 401, not 403 or 500."""
    resp = client_auth_required.get(
        "/health",
        headers={"Authorization": "Bearer garbage.token.here"},
    )
    assert resp.status_code == 401


def test_expired_token_returns_401(db_session, client_auth_required):
    """An expired token must produce 401."""
    from datetime import timedelta

    user = _seed_user(db_session, "owner")
    expired = create_access_token(
        sub=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value,
        expires_delta=timedelta(seconds=-1),
    )
    resp = client_auth_required.get(
        "/health",
        headers=_auth_header(expired),
    )
    assert resp.status_code == 401


def test_deleted_user_returns_401(db_session, client_auth_required):
    """A token for a deleted user must produce 401."""
    user = _seed_user(db_session, "admin")
    token = _token_for(user)
    db_session.delete(user)
    db_session.commit()
    resp = client_auth_required.get(
        "/health",
        headers=_auth_header(token),
    )
    assert resp.status_code == 401


def test_viewer_cannot_apply_prices(db_session, client_auth_required):
    """Explicit acceptance criteria: viewer cannot apply."""
    user = _seed_user(db_session, "viewer")
    token = _token_for(user)
    resp = client_auth_required.post(
        "/apply-prices/apply",
        json={},
        headers=_auth_header(token),
    )
    assert resp.status_code == 403


def test_operator_cannot_apply_prices(db_session, client_auth_required):
    """Explicit acceptance criteria: operator cannot apply."""
    user = _seed_user(db_session, "operator")
    token = _token_for(user)
    resp = client_auth_required.post(
        "/apply-prices/apply",
        json={},
        headers=_auth_header(token),
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_owner_can_reach_apply(db_session, client_auth_required, role):
    """Admin/owner can reach /apply-prices/apply (past the RBAC layer)."""
    user = _seed_user(db_session, role)
    token = _token_for(user)
    resp = client_auth_required.post(
        "/apply-prices/apply",
        json={},
        headers=_auth_header(token),
    )
    # Should NOT be 401 or 403 — downstream validation errors are fine.
    assert resp.status_code not in (401, 403)


def test_tenant_scoping_sets_request_state(db_session, client_auth_required):
    """When auth is required, the RBAC dependency sets request.state.tenant_id."""
    user = _seed_user(db_session, "viewer")
    token = _token_for(user)
    # /auth/me is a good probe — it reads the authenticated user.
    resp = client_auth_required.get("/auth/me", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["tenant_id"] == str(user.tenant_id)
