"""SQLAlchemy models for SB-Optima SaaS primitives (Task 4.1).

Defines:
- :class:`Tenant` — a billable organisation.
- :class:`User`   — a person belonging to exactly one tenant.
- :class:`Role`   — Python enum mapped to a DB-level enum.

These models use ``sqlalchemy.dialects.postgresql.UUID`` for Postgres and
fall back transparently to a ``CHAR(32)`` representation on SQLite so that
the test suite can run without a real Postgres instance.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.db import Base

# ---------------------------------------------------------------------------
# UUID column type — Postgres-native or string fallback
# ---------------------------------------------------------------------------


class _GUID(sa.types.TypeDecorator):
    """Platform-independent UUID type.

    Uses Postgres' native ``UUID`` type when available, otherwise stores
    as ``CHAR(32)`` (hex without dashes).
    """

    impl = sa.types.CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID

            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(sa.types.CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
        # SQLite / other — store as hex string
        if isinstance(value, uuid.UUID):
            return value.hex
        return uuid.UUID(value).hex

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(value)


# ---------------------------------------------------------------------------
# Role enum
# ---------------------------------------------------------------------------


class Role(str, enum.Enum):
    """User role within a tenant.

    Roles are ordered from most to least privileged:
    - ``owner``    — full control, billing, can delete tenant.
    - ``admin``    — manage users and configuration.
    - ``operator`` — day-to-day operations (run optimisations, apply prices).
    - ``viewer``   — read-only access to dashboards and reports.

    The default role for new users is **operator** (a safe middle ground).
    """

    owner = "owner"
    admin = "admin"
    operator = "operator"
    viewer = "viewer"


# ---------------------------------------------------------------------------
# Tenant model
# ---------------------------------------------------------------------------


class Tenant(Base):
    """A billable organisation (SaaS tenant)."""

    __tablename__ = "tenants"

    id = Column(_GUID(), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )
    stripe_customer_id = Column(String(255), nullable=True)
    plan = Column(String(50), nullable=True)
    status = Column(String(50), nullable=True)

    # Relationships
    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    credentials = relationship(
        "HostedShopCredential", back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id!r} name={self.name!r}>"


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


class User(Base):
    """A person belonging to a single tenant."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    id = Column(_GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        _GUID(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email = Column(String(320), nullable=False)
    password_hash = Column(String(512), nullable=False)
    role = Column(
        Enum(Role, name="user_role", native_enum=False, length=20),
        nullable=False,
        default=Role.operator,
        server_default="operator",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="users")

    def __repr__(self) -> str:
        return f"<User id={self.id!r} email={self.email!r} role={self.role!r}>"


# ---------------------------------------------------------------------------
# HostedShop credential model (Task 5.1 — Credential Vault)
# ---------------------------------------------------------------------------


class HostedShopCredential(Base):
    """Encrypted HostedShop SOAP credentials scoped to a tenant."""

    __tablename__ = "hostedshop_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_creds_tenant_name"),
    )

    id = Column(_GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        _GUID(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    site_id = Column(String(50), nullable=False)
    api_username_enc = Column(String(1024), nullable=False)
    api_password_enc = Column(String(1024), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
    )

    # Relationships
    tenant = relationship("Tenant", back_populates="credentials")

    def __repr__(self) -> str:
        return f"<HostedShopCredential id={self.id!r} name={self.name!r}>"
