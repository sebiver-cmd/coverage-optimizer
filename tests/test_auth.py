"""Tests for Task 4.2 — Authentication (JWT): signup, login, refresh, protected routes.

All tests use an in-memory SQLite database (no Postgres required).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from backend.config import get_settings
from backend.db import Base, get_db
from backend.main import app
from backend.models import Role, Tenant, User

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SQLITE_URL = "sqlite://"


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


@pytest.fixture()
def client(db_session: Session, monkeypatch):
    """FastAPI TestClient with get_db overridden and dev JWT secret."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    get_settings.cache_clear()

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper to create a user directly in the DB for login tests
# ---------------------------------------------------------------------------


def _seed_user(
    db: Session,
    *,
    email: str = "test@example.com",
    password: str = "Str0ngP@ss!",
    role: Role = Role.operator,
    tenant_name: str = "TestTenant",
) -> tuple[User, Tenant]:
    tenant = Tenant(name=tenant_name)
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(tenant)
    return user, tenant


# ===========================================================================
# Unit tests — password hashing
# ===========================================================================


class TestPasswordHashing:
    def test_hash_and_verify(self):
        h = hash_password("mypassword")
        assert h != "mypassword"
        assert verify_password("mypassword", h)

    def test_wrong_password_fails(self):
        h = hash_password("correct")
        assert not verify_password("wrong", h)

    def test_hash_is_different_each_time(self):
        """bcrypt includes a random salt so hashes differ."""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        # Both still verify
        assert verify_password("same", h1)
        assert verify_password("same", h2)


# ===========================================================================
# Unit tests — JWT creation & decoding
# ===========================================================================


class TestJWT:
    def test_create_and_decode(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
        settings = get_settings()

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        token = create_access_token(sub=uid, tenant_id=tid, role="operator", settings=settings)
        payload = decode_token(token, settings=settings)

        assert payload["sub"] == str(uid)
        assert payload["tenant_id"] == str(tid)
        assert payload["role"] == "operator"
        assert "exp" in payload
        assert "iat" in payload

    def test_expired_token_raises(self, monkeypatch):
        from jose import JWTError

        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
        settings = get_settings()

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        token = create_access_token(
            sub=uid,
            tenant_id=tid,
            role="viewer",
            settings=settings,
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(JWTError):
            decode_token(token, settings=settings)

    def test_bad_signature_raises(self, monkeypatch):
        from jose import JWTError

        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
        settings = get_settings()

        uid = uuid.uuid4()
        tid = uuid.uuid4()
        token = create_access_token(sub=uid, tenant_id=tid, role="admin", settings=settings)

        # Tamper with the token
        tampered = token + "TAMPERED"
        with pytest.raises(JWTError):
            decode_token(tampered, settings=settings)

    def test_jwt_secret_required_in_prod(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_ENV", "prod")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
        settings = get_settings()

        with pytest.raises(ValueError, match="JWT_SECRET must be set"):
            settings.get_jwt_secret()

    def test_jwt_secret_dev_fallback(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.get_jwt_secret() == "dev-secret"

    def test_jwt_secret_explicit_overrides_default(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.setenv("JWT_SECRET", "my-secret-key")
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.get_jwt_secret() == "my-secret-key"


# ===========================================================================
# API tests — POST /auth/signup
# ===========================================================================


class TestSignup:
    def test_signup_success(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "Acme Corp",
                "email": "owner@acme.com",
                "password": "Str0ngP@ss!",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_signup_creates_tenant_and_owner(self, client: TestClient, db_session: Session):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "NewOrg",
                "email": "boss@neworg.com",
                "password": "Passw0rd!",
            },
        )
        assert resp.status_code == 201

        # Verify DB state
        user = db_session.query(User).filter(User.email == "boss@neworg.com").first()
        assert user is not None
        assert user.role == Role.owner
        assert user.tenant.name == "NewOrg"

    def test_signup_email_normalised_lowercase(self, client: TestClient, db_session: Session):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "CaseTenant",
                "email": "UPPER@Example.COM",
                "password": "Passw0rd!",
            },
        )
        assert resp.status_code == 201
        user = db_session.query(User).filter(User.email == "upper@example.com").first()
        assert user is not None

    def test_signup_short_password_rejected(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={"tenant_name": "T", "email": "a@b.com", "password": "short"},
        )
        assert resp.status_code == 422

    def test_signup_invalid_email_rejected(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={"tenant_name": "T", "email": "not-an-email", "password": "Str0ngP@ss!"},
        )
        assert resp.status_code == 422

    def test_signup_token_is_usable(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "UsableToken",
                "email": "usable@example.com",
                "password": "Str0ngP@ss!",
            },
        )
        token = resp.json()["access_token"]
        me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "usable@example.com"


# ===========================================================================
# API tests — POST /auth/login
# ===========================================================================


class TestLogin:
    def test_login_success(self, client: TestClient, db_session: Session):
        _seed_user(db_session, email="login@test.com", password="ValidPass1!")
        resp = client.post(
            "/auth/login",
            json={"email": "login@test.com", "password": "ValidPass1!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client: TestClient, db_session: Session):
        _seed_user(db_session, email="wrong@test.com", password="CorrectPass1!")
        resp = client.post(
            "/auth/login",
            json={"email": "wrong@test.com", "password": "WrongPass1!"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid email or password"

    def test_login_unknown_email(self, client: TestClient):
        resp = client.post(
            "/auth/login",
            json={"email": "nobody@test.com", "password": "Whatever1!"},
        )
        assert resp.status_code == 401

    def test_login_case_insensitive_email(self, client: TestClient, db_session: Session):
        _seed_user(db_session, email="lower@test.com", password="ValidPass1!")
        resp = client.post(
            "/auth/login",
            json={"email": "LOWER@test.com", "password": "ValidPass1!"},
        )
        # Our seed stores email as-is, but login normalises input.
        # If the seed email is already lowercase, this should work.
        assert resp.status_code == 200

    def test_login_token_contains_correct_claims(self, client: TestClient, db_session: Session, monkeypatch):
        user, tenant = _seed_user(db_session, email="claims@test.com", password="ValidPass1!")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()

        resp = client.post(
            "/auth/login",
            json={"email": "claims@test.com", "password": "ValidPass1!"},
        )
        token = resp.json()["access_token"]
        payload = decode_token(token)
        assert payload["sub"] == str(user.id)
        assert payload["tenant_id"] == str(tenant.id)
        assert payload["role"] == user.role.value


# ===========================================================================
# API tests — POST /auth/refresh
# ===========================================================================


class TestRefresh:
    def test_refresh_returns_new_token(self, client: TestClient, db_session: Session):
        _seed_user(db_session, email="refresh@test.com", password="ValidPass1!")
        login_resp = client.post(
            "/auth/login",
            json={"email": "refresh@test.com", "password": "ValidPass1!"},
        )
        old_token = login_resp.json()["access_token"]

        refresh_resp = client.post(
            "/auth/refresh",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert refresh_resp.status_code == 200
        new_token = refresh_resp.json()["access_token"]
        assert new_token  # non-empty

        # The new token must also be usable
        me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "refresh@test.com"

    def test_refresh_without_token_401(self, client: TestClient):
        resp = client.post("/auth/refresh")
        assert resp.status_code == 401


# ===========================================================================
# API tests — GET /auth/me (protected route)
# ===========================================================================


class TestMe:
    def test_me_with_valid_token(self, client: TestClient, db_session: Session):
        user, _ = _seed_user(db_session, email="me@test.com", password="ValidPass1!")
        login_resp = client.post(
            "/auth/login",
            json={"email": "me@test.com", "password": "ValidPass1!"},
        )
        token = login_resp.json()["access_token"]
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "me@test.com"
        assert data["id"] == str(user.id)
        assert data["role"] == "operator"

    def test_me_without_token_401(self, client: TestClient):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token_401(self, client: TestClient):
        resp = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer this.is.garbage"},
        )
        assert resp.status_code == 401

    def test_me_with_expired_token_401(self, client: TestClient, db_session: Session, monkeypatch):
        user, _ = _seed_user(db_session, email="expire@test.com", password="ValidPass1!")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()
        settings = get_settings()

        expired_token = create_access_token(
            sub=user.id,
            tenant_id=user.tenant_id,
            role=user.role.value,
            settings=settings,
            expires_delta=timedelta(seconds=-1),
        )
        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    def test_me_with_deleted_user_401(self, client: TestClient, db_session: Session, monkeypatch):
        user, tenant = _seed_user(db_session, email="deleted@test.com", password="ValidPass1!")
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        get_settings.cache_clear()
        settings = get_settings()

        token = create_access_token(
            sub=user.id,
            tenant_id=tenant.id,
            role=user.role.value,
            settings=settings,
        )
        # Delete the user
        db_session.delete(user)
        db_session.commit()

        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


# ===========================================================================
# Tenant isolation
# ===========================================================================


class TestTenantIsolation:
    def test_signup_creates_separate_tenants(self, client: TestClient, db_session: Session):
        """Two signups create two distinct tenants."""
        r1 = client.post(
            "/auth/signup",
            json={"tenant_name": "Org1", "email": "a@org1.com", "password": "Str0ngP@ss!"},
        )
        r2 = client.post(
            "/auth/signup",
            json={"tenant_name": "Org2", "email": "b@org2.com", "password": "Str0ngP@ss!"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        t1 = decode_token(r1.json()["access_token"])
        t2 = decode_token(r2.json()["access_token"])
        assert t1["tenant_id"] != t2["tenant_id"]

    def test_me_returns_correct_tenant(self, client: TestClient, db_session: Session):
        user, tenant = _seed_user(db_session, email="iso@test.com", password="ValidPass1!", tenant_name="IsoTenant")
        login = client.post(
            "/auth/login",
            json={"email": "iso@test.com", "password": "ValidPass1!"},
        )
        token = login.json()["access_token"]
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json()["tenant_id"] == str(tenant.id)


# ===========================================================================
# Config edge cases
# ===========================================================================


class TestAuthConfig:
    def test_default_auth_not_required(self, monkeypatch):
        monkeypatch.delenv("SBOPTIMA_AUTH_REQUIRED", raising=False)
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.sboptima_auth_required is False

    def test_auth_required_flag(self, monkeypatch):
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.sboptima_auth_required is True

    def test_jwt_algorithm_default(self, monkeypatch):
        monkeypatch.delenv("JWT_ALGORITHM", raising=False)
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.jwt_algorithm == "HS256"

    def test_jwt_exp_default(self, monkeypatch):
        monkeypatch.delenv("JWT_ACCESS_TOKEN_EXP_MINUTES", raising=False)
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.jwt_access_token_exp_minutes == 60


# ===========================================================================
# Password byte-length validation (bcrypt 72-byte limit)
# ===========================================================================


class TestPasswordByteLimit:
    """Ensure passwords longer than 72 UTF-8 bytes are rejected with 422, not 500."""

    # A string of 73 ASCII characters is also 73 bytes.
    _LONG_ASCII_PASSWORD = "A" * 73
    # A string using 2-byte UTF-8 characters that exceeds 72 bytes with fewer chars.
    # "é" is 2 bytes in UTF-8, so 37 × "é" = 74 bytes.
    _LONG_UNICODE_PASSWORD = "é" * 37

    def test_signup_over_72_byte_ascii_password_returns_422(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "ByteTest",
                "email": "bytetest@example.com",
                "password": self._LONG_ASCII_PASSWORD,
            },
        )
        assert resp.status_code == 422

    def test_signup_over_72_byte_unicode_password_returns_422(self, client: TestClient):
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "UnicodeTest",
                "email": "unicode@example.com",
                "password": self._LONG_UNICODE_PASSWORD,
            },
        )
        assert resp.status_code == 422

    def test_signup_exactly_72_byte_password_succeeds(self, client: TestClient):
        """A password of exactly 72 ASCII bytes is within the limit."""
        password_72 = "A" * 64 + "1!aB" + "zZyY"  # 72 chars = 72 bytes
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "Boundary72",
                "email": "boundary72@example.com",
                "password": password_72,
            },
        )
        assert resp.status_code == 201

    def test_signup_normal_password_returns_201(self, client: TestClient):
        """A standard short password produces a successful signup."""
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "Normal",
                "email": "normal@example.com",
                "password": "Str0ngP@ss!",
            },
        )
        assert resp.status_code == 201
        assert "access_token" in resp.json()

    def test_login_over_72_byte_password_returns_422(self, client: TestClient, db_session: Session):
        """A >72-byte password on login is rejected at validation, not as 401/500."""
        _seed_user(db_session, email="longpwd@example.com", password="ValidPass1!")
        resp = client.post(
            "/auth/login",
            json={"email": "longpwd@example.com", "password": self._LONG_ASCII_PASSWORD},
        )
        assert resp.status_code == 422

    def test_error_response_does_not_echo_password(self, client: TestClient):
        """The 422 error body must not contain the submitted password."""
        resp = client.post(
            "/auth/signup",
            json={
                "tenant_name": "NoLeak",
                "email": "noleak@example.com",
                "password": self._LONG_ASCII_PASSWORD,
            },
        )
        assert resp.status_code == 422
        assert self._LONG_ASCII_PASSWORD not in resp.text
