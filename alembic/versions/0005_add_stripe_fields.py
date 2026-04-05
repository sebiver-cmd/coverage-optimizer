"""add stripe subscription and billing_status columns to tenants

Revision ID: 0005_stripe_fields
Revises: 0004_tenant_limits
Create Date: 2026-04-05

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_stripe_fields"
down_revision: Union[str, None] = "0004_tenant_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("billing_status", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "billing_status")
    op.drop_column("tenants", "stripe_subscription_id")
