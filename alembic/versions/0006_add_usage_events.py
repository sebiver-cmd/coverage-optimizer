"""add usage_events table for billing/metering

Revision ID: 0006_usage_events
Revises: 0005_stripe_fields
Create Date: 2026-04-06

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_usage_events"
down_revision: Union[str, None] = "0005_stripe_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.CHAR(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.CHAR(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.Column("meta_json", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("usage_events")
