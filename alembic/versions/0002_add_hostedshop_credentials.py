"""add hostedshop_credentials

Revision ID: 0002_hostedshop_creds
Revises: 0001_tenants_users
Create Date: 2026-04-05

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_hostedshop_creds"
down_revision: Union[str, None] = "0001_tenants_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hostedshop_credentials",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("site_id", sa.String(50), nullable=False),
        sa.Column("api_username_enc", sa.String(1024), nullable=False),
        sa.Column("api_password_enc", sa.String(1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_hostedshop_credentials_tenant_id",
        "hostedshop_credentials",
        ["tenant_id"],
    )
    op.create_unique_constraint(
        "uq_creds_tenant_name",
        "hostedshop_credentials",
        ["tenant_id", "name"],
    )


def downgrade() -> None:
    op.drop_table("hostedshop_credentials")
