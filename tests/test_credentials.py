"""Tests for Task 5.1 — Credential Vault (tenant-scoped).

Covers:
1. Encryption round-trip and wrong-key rejection.
2. Credential CRUD — metadata only, no secrets in responses.
3. Tenant isolation — cross-tenant access blocked.
4. Credential resolver logic (vault, raw, legacy).
5. RBAC — viewer/operator cannot manage credentials.
6. Edge cases — missing ENCRYPTION_KEY, auth disabled, etc.

All tests use an in-memory SQLite database (no Postgres required).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import create_access_token, hash_password
from backend.config import get_settings
from backend.credential_resolver import resolve_hostedshop_credentials
from backend.credentials_api import router as credentials_router
from backend.crypto import decrypt_str, encrypt_str
from backend.db import Base, get_db
from backend.main import app
from backend.models import HostedShopCredential, Role, Tenant, User

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


def _make_client(db_session: Session, monkeypatch, *, auth_required: bool, encryption_key: str | None = _FERNET_KEY):
    """Build a TestClient with the given settings."""
    monkeypatch.setenv("SBOPTIMA_ENV", "dev")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false")
    if encryption_key:
        monkeypatch.setenv("ENCRYPTION_KEY", encryption_key)
    else:
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED", raising=False)
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
def client_auth_on(db_session, monkeypatch):
    """TestClient with auth ON and valid encryption key."""
    client = _make_client(db_session, monkeypatch, auth_required=True)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_auth_off(db_session, monkeypatch):
    """TestClient with auth OFF."""
    client = _make_client(db_session, monkeypatch, auth_required=False)
    yield client
    app.dependency_overrides.clear()


def _seed_tenant_and_user(db: Session, *, role: str = "admin", email: str = "admin@test.com") -> User:
    """Create a tenant + user with the given role."""
    tenant = Tenant(name=f"Tenant-{role}-{uuid.uuid4().hex[:6]}")
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=email,
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


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token_for(user)}"}


# ===========================================================================
# 1. Encryption tests
# ===========================================================================


class TestEncryption:
    """Verify encrypt_str / decrypt_str round-trip and key validation."""

    def test_round_trip(self, monkeypatch):
        """Encrypt then decrypt returns original plaintext."""
        monkeypatch.setenv("ENCRYPTION_KEY", _FERNET_KEY)
        get_settings.cache_clear()

        original = "my-secret-password-1234!@#"
        token = encrypt_str(original)
        assert token != original, "ciphertext must differ from plaintext"
        assert decrypt_str(token) == original

    def test_wrong_key_fails(self, monkeypatch):
        """Decrypt with a different key raises an error."""
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()
        assert key1 != key2

        monkeypatch.setenv("ENCRYPTION_KEY", key1)
        get_settings.cache_clear()
        token = encrypt_str("secret")

        monkeypatch.setenv("ENCRYPTION_KEY", key2)
        get_settings.cache_clear()
        with pytest.raises(Exception):  # InvalidToken
            decrypt_str(token)

    def test_missing_key_raises(self, monkeypatch):
        """Encrypt/decrypt without ENCRYPTION_KEY raises ValueError."""
        monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        get_settings.cache_clear()

        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            encrypt_str("anything")

    def test_invalid_key_raises(self, monkeypatch):
        """An invalid Fernet key raises ValueError."""
        monkeypatch.setenv("ENCRYPTION_KEY", "not-a-valid-key")
        get_settings.cache_clear()

        with pytest.raises(ValueError, match="valid Fernet key"):
            encrypt_str("anything")


# ===========================================================================
# 2. Credential CRUD tests
# ===========================================================================


class TestCredentialCRUD:
    """Test create / list / delete via the /credentials endpoints."""

    def test_create_returns_metadata_only(self, db_session, client_auth_on):
        """POST /credentials returns id, name, site_id — never secrets."""
        user = _seed_tenant_and_user(db_session)
        resp = client_auth_on.post(
            "/credentials/",
            json={
                "name": "Main shop",
                "site_id": "1",
                "api_username": "user@example.com",
                "api_password": "s3cret",
            },
            headers=_auth(user),
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert "id" in data
        assert data["name"] == "Main shop"
        assert data["site_id"] == "1"
        assert "api_username" not in data
        assert "api_password" not in data
        assert "api_username_enc" not in data
        assert "api_password_enc" not in data

    def test_list_returns_metadata_only(self, db_session, client_auth_on):
        """GET /credentials returns a list with no secrets."""
        user = _seed_tenant_and_user(db_session)
        headers = _auth(user)
        client_auth_on.post(
            "/credentials/",
            json={"name": "Shop A", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=headers,
        )
        client_auth_on.post(
            "/credentials/",
            json={"name": "Shop B", "site_id": "2", "api_username": "u2", "api_password": "p2"},
            headers=headers,
        )
        resp = client_auth_on.get("/credentials/", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        for item in items:
            assert "api_username" not in item
            assert "api_password" not in item

    def test_delete(self, db_session, client_auth_on):
        """DELETE /credentials/{id} removes the credential."""
        user = _seed_tenant_and_user(db_session)
        headers = _auth(user)
        create_resp = client_auth_on.post(
            "/credentials/",
            json={"name": "Temp", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=headers,
        )
        cred_id = create_resp.json()["id"]

        del_resp = client_auth_on.delete(f"/credentials/{cred_id}", headers=headers)
        assert del_resp.status_code == 204

        # Verify it's gone
        list_resp = client_auth_on.get("/credentials/", headers=headers)
        assert len(list_resp.json()) == 0

    def test_duplicate_name_rejected(self, db_session, client_auth_on):
        """Creating two credentials with the same name returns 409."""
        user = _seed_tenant_and_user(db_session)
        headers = _auth(user)
        body = {"name": "Duplicate", "site_id": "1", "api_username": "u", "api_password": "p"}
        assert client_auth_on.post("/credentials/", json=body, headers=headers).status_code == 201
        resp = client_auth_on.post("/credentials/", json=body, headers=headers)
        assert resp.status_code == 409

    def test_ciphertext_differs_from_plaintext(self, db_session, client_auth_on):
        """Verify that stored values in DB are encrypted (not plaintext)."""
        user = _seed_tenant_and_user(db_session)
        headers = _auth(user)
        client_auth_on.post(
            "/credentials/",
            json={"name": "Check", "site_id": "1", "api_username": "clearuser", "api_password": "clearpass"},
            headers=headers,
        )
        # Query the DB directly
        cred = db_session.query(HostedShopCredential).first()
        assert cred is not None
        assert cred.api_username_enc != "clearuser"
        assert cred.api_password_enc != "clearpass"

    def test_delete_nonexistent_returns_404(self, db_session, client_auth_on):
        """DELETE for a non-existent credential returns 404."""
        user = _seed_tenant_and_user(db_session)
        resp = client_auth_on.delete(
            f"/credentials/{uuid.uuid4()}",
            headers=_auth(user),
        )
        assert resp.status_code == 404


# ===========================================================================
# 3. Tenant isolation tests
# ===========================================================================


class TestTenantIsolation:
    """Verify that credentials are scoped to the caller's tenant."""

    def test_cannot_list_other_tenant_creds(self, db_session, client_auth_on):
        """User from tenant A cannot see credentials from tenant B."""
        user_a = _seed_tenant_and_user(db_session, email="a@a.com")
        user_b = _seed_tenant_and_user(db_session, email="b@b.com")

        # User A creates a credential
        client_auth_on.post(
            "/credentials/",
            json={"name": "A-cred", "site_id": "1", "api_username": "ua", "api_password": "pa"},
            headers=_auth(user_a),
        )

        # User B lists — should see nothing
        resp = client_auth_on.get("/credentials/", headers=_auth(user_b))
        assert resp.status_code == 200
        assert len(resp.json()) == 0

    def test_cannot_delete_other_tenant_cred(self, db_session, client_auth_on):
        """User from tenant B cannot delete tenant A's credential."""
        user_a = _seed_tenant_and_user(db_session, email="a2@a.com")
        user_b = _seed_tenant_and_user(db_session, email="b2@b.com")

        create_resp = client_auth_on.post(
            "/credentials/",
            json={"name": "A-cred2", "site_id": "1", "api_username": "ua", "api_password": "pa"},
            headers=_auth(user_a),
        )
        cred_id = create_resp.json()["id"]

        # User B tries to delete → 404 (not found for their tenant)
        del_resp = client_auth_on.delete(f"/credentials/{cred_id}", headers=_auth(user_b))
        assert del_resp.status_code == 404

        # Credential still exists for user A
        list_resp = client_auth_on.get("/credentials/", headers=_auth(user_a))
        assert len(list_resp.json()) == 1


# ===========================================================================
# 4. Credential resolver tests
# ===========================================================================


class TestCredentialResolver:
    """Test resolve_hostedshop_credentials logic."""

    def _make_settings(self, monkeypatch, *, auth_required: bool, encryption_key: str | None = _FERNET_KEY, allow_raw: bool = False):
        monkeypatch.setenv("SBOPTIMA_ENV", "dev")
        monkeypatch.setenv("SBOPTIMA_AUTH_REQUIRED", "true" if auth_required else "false")
        if encryption_key:
            monkeypatch.setenv("ENCRYPTION_KEY", encryption_key)
        else:
            monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED", "true" if allow_raw else "false")
        get_settings.cache_clear()
        return get_settings()

    def test_auth_off_uses_raw_creds(self, monkeypatch):
        """When auth is off, raw creds from request are used (legacy)."""
        settings = self._make_settings(monkeypatch, auth_required=False)
        payload = SimpleNamespace(api_username="user", api_password="pass", site_id=1, credential_id=None)
        site_id, username, password = resolve_hostedshop_credentials(
            request_payload=payload, current_user=None, db=None, settings=settings,
        )
        assert username == "user"
        assert password == "pass"
        assert site_id == "1"

    def test_auth_off_missing_creds_raises(self, monkeypatch):
        """When auth is off and no raw creds, raises 400."""
        settings = self._make_settings(monkeypatch, auth_required=False)
        payload = SimpleNamespace(api_username="", api_password="", site_id=1, credential_id=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_hostedshop_credentials(
                request_payload=payload, current_user=None, db=None, settings=settings,
            )
        assert exc_info.value.status_code == 400

    def test_auth_on_credential_id_uses_vault(self, db_session, monkeypatch):
        """When auth is on + credential_id, uses decrypted vault creds."""
        settings = self._make_settings(monkeypatch, auth_required=True)
        user = _seed_tenant_and_user(db_session)

        # Create an encrypted credential directly
        cred = HostedShopCredential(
            tenant_id=user.tenant_id,
            name="Test",
            site_id="42",
            api_username_enc=encrypt_str("vault-user"),
            api_password_enc=encrypt_str("vault-pass"),
        )
        db_session.add(cred)
        db_session.commit()
        db_session.refresh(cred)

        payload = SimpleNamespace(
            api_username="", api_password="", site_id=1, credential_id=cred.id,
        )
        site_id, username, password = resolve_hostedshop_credentials(
            request_payload=payload, current_user=user, db=db_session, settings=settings,
        )
        assert username == "vault-user"
        assert password == "vault-pass"
        assert site_id == "42"

    def test_auth_on_raw_creds_rejected_by_default(self, monkeypatch):
        """When auth is on, raw creds are rejected unless flag is set."""
        settings = self._make_settings(monkeypatch, auth_required=True, allow_raw=False)
        user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.admin)
        payload = SimpleNamespace(
            api_username="user", api_password="pass", site_id=1, credential_id=None,
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_hostedshop_credentials(
                request_payload=payload, current_user=user, db=None, settings=settings,
            )
        assert exc_info.value.status_code == 400
        assert "not allowed" in exc_info.value.detail.lower()

    def test_auth_on_raw_creds_allowed_when_flag_set(self, monkeypatch):
        """When allow_request_credentials_when_authed=True, raw creds work."""
        settings = self._make_settings(monkeypatch, auth_required=True, allow_raw=True)
        user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.admin)
        payload = SimpleNamespace(
            api_username="user", api_password="pass", site_id=1, credential_id=None,
        )
        site_id, username, password = resolve_hostedshop_credentials(
            request_payload=payload, current_user=user, db=None, settings=settings,
        )
        assert username == "user"
        assert password == "pass"

    def test_auth_on_wrong_tenant_returns_404(self, db_session, monkeypatch):
        """credential_id from another tenant returns 404."""
        settings = self._make_settings(monkeypatch, auth_required=True)
        user_a = _seed_tenant_and_user(db_session, email="ra@a.com")
        user_b = _seed_tenant_and_user(db_session, email="rb@b.com")

        cred = HostedShopCredential(
            tenant_id=user_a.tenant_id,
            name="A-cred",
            site_id="1",
            api_username_enc=encrypt_str("u"),
            api_password_enc=encrypt_str("p"),
        )
        db_session.add(cred)
        db_session.commit()
        db_session.refresh(cred)

        payload = SimpleNamespace(
            api_username="", api_password="", site_id=1, credential_id=cred.id,
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_hostedshop_credentials(
                request_payload=payload, current_user=user_b, db=db_session, settings=settings,
            )
        assert exc_info.value.status_code == 404

    def test_auth_on_no_creds_at_all_raises(self, monkeypatch):
        """When auth on and neither credential_id nor raw creds, raises 400."""
        settings = self._make_settings(monkeypatch, auth_required=True)
        user = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.admin)
        payload = SimpleNamespace(api_username="", api_password="", site_id=1, credential_id=None)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_hostedshop_credentials(
                request_payload=payload, current_user=user, db=None, settings=settings,
            )
        assert exc_info.value.status_code == 400

    def test_auth_on_missing_encryption_key_raises(self, db_session, monkeypatch):
        """credential_id without ENCRYPTION_KEY raises 500."""
        settings = self._make_settings(monkeypatch, auth_required=True, encryption_key=None)
        user = _seed_tenant_and_user(db_session, email="nokey@test.com")
        payload = SimpleNamespace(
            api_username="", api_password="", site_id=1, credential_id=uuid.uuid4(),
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            resolve_hostedshop_credentials(
                request_payload=payload, current_user=user, db=db_session, settings=settings,
            )
        assert exc_info.value.status_code == 500
        assert "ENCRYPTION_KEY" in exc_info.value.detail


# ===========================================================================
# 5. RBAC tests
# ===========================================================================


class TestCredentialRBAC:
    """Verify role enforcement on credential endpoints."""

    def test_viewer_cannot_create(self, db_session, monkeypatch):
        """Viewer role should be rejected (403)."""
        client = _make_client(db_session, monkeypatch, auth_required=True)
        user = _seed_tenant_and_user(db_session, role="viewer", email="v@test.com")
        resp = client.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=_auth(user),
        )
        assert resp.status_code == 403
        app.dependency_overrides.clear()

    def test_operator_cannot_create(self, db_session, monkeypatch):
        """Operator role should be rejected (403)."""
        client = _make_client(db_session, monkeypatch, auth_required=True)
        user = _seed_tenant_and_user(db_session, role="operator", email="o@test.com")
        resp = client.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=_auth(user),
        )
        assert resp.status_code == 403
        app.dependency_overrides.clear()

    def test_admin_can_create(self, db_session, client_auth_on):
        """Admin role should succeed."""
        user = _seed_tenant_and_user(db_session, role="admin", email="adm@test.com")
        resp = client_auth_on.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=_auth(user),
        )
        assert resp.status_code == 201

    def test_owner_can_create(self, db_session, client_auth_on):
        """Owner role should succeed."""
        user = _seed_tenant_and_user(db_session, role="owner", email="own@test.com")
        resp = client_auth_on.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=_auth(user),
        )
        assert resp.status_code == 201

    def test_viewer_cannot_list(self, db_session, monkeypatch):
        """Viewer role should be rejected for listing."""
        client = _make_client(db_session, monkeypatch, auth_required=True)
        user = _seed_tenant_and_user(db_session, role="viewer", email="vl@test.com")
        resp = client.get("/credentials/", headers=_auth(user))
        assert resp.status_code == 403
        app.dependency_overrides.clear()

    def test_viewer_cannot_delete(self, db_session, monkeypatch):
        """Viewer role should be rejected for deletion."""
        client = _make_client(db_session, monkeypatch, auth_required=True)
        user = _seed_tenant_and_user(db_session, role="viewer", email="vd@test.com")
        resp = client.delete(f"/credentials/{uuid.uuid4()}", headers=_auth(user))
        assert resp.status_code == 403
        app.dependency_overrides.clear()


# ===========================================================================
# 6. Auth-off vault endpoints return 503
# ===========================================================================


class TestVaultAuthOff:
    """When auth is disabled, vault endpoints return 503."""

    def test_create_returns_503(self, client_auth_off):
        resp = client_auth_off.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
        )
        assert resp.status_code == 503

    def test_list_returns_503(self, client_auth_off):
        resp = client_auth_off.get("/credentials/")
        assert resp.status_code == 503

    def test_delete_returns_503(self, client_auth_off):
        resp = client_auth_off.delete(f"/credentials/{uuid.uuid4()}")
        assert resp.status_code == 503


# ===========================================================================
# 7. Missing ENCRYPTION_KEY when auth is on
# ===========================================================================


class TestMissingEncryptionKey:
    """Vault ops fail with 500 when ENCRYPTION_KEY is not set."""

    def test_create_without_encryption_key(self, db_session, monkeypatch):
        """POST /credentials without ENCRYPTION_KEY returns 500."""
        client = _make_client(db_session, monkeypatch, auth_required=True, encryption_key=None)
        user = _seed_tenant_and_user(db_session, email="nokey@test.com")
        resp = client.post(
            "/credentials/",
            json={"name": "x", "site_id": "1", "api_username": "u", "api_password": "p"},
            headers=_auth(user),
        )
        assert resp.status_code == 500
        assert "ENCRYPTION_KEY" in resp.json()["detail"]
        app.dependency_overrides.clear()

    def test_list_without_encryption_key(self, db_session, monkeypatch):
        """GET /credentials without ENCRYPTION_KEY returns 500."""
        client = _make_client(db_session, monkeypatch, auth_required=True, encryption_key=None)
        user = _seed_tenant_and_user(db_session, email="nokey2@test.com")
        resp = client.get("/credentials/", headers=_auth(user))
        assert resp.status_code == 500
        assert "ENCRYPTION_KEY" in resp.json()["detail"]
        app.dependency_overrides.clear()
