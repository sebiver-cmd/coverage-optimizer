"""add tenant usage-limit columns

Revision ID: 0004_tenant_limits
Revises: 0003_jobs_and_batches
Create Date: 2026-04-05

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_tenant_limits"
down_revision: Union[str, None] = "0003_jobs_and_batches"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("daily_optimize_jobs_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("daily_apply_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenants",
        sa.Column("daily_optimize_sync_limit", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenants", "daily_optimize_sync_limit")
    op.drop_column("tenants", "daily_apply_limit")
    op.drop_column("tenants", "daily_optimize_jobs_limit")
