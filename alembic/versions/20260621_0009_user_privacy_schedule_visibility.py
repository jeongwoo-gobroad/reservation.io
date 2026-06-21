"""add user privacy and schedule visibility fields

Revision ID: 20260621_0009
Revises: 20260621_0008
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260621_0009"
down_revision = "20260621_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "schedule_sets",
        sa.Column("show_stock_remaining", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "schedule_sets",
        sa.Column("show_time_slot_remaining", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("schedule_sets", "show_time_slot_remaining")
    op.drop_column("schedule_sets", "show_stock_remaining")
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_deleted")
