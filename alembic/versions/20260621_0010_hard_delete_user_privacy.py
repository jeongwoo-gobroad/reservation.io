"""hard delete previously soft-deleted users

Revision ID: 20260621_0010
Revises: 20260621_0009
Create Date: 2026-06-21
"""

from alembic import op
import sqlalchemy as sa


revision = "20260621_0010"
down_revision = "20260621_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            DELETE FROM completed_visits
            WHERE user_id IN (SELECT id FROM users WHERE is_deleted = true)
               OR user_uuid IN (SELECT uuid FROM users WHERE is_deleted = true)
            """
        )
    )
    conn.execute(sa.text("DELETE FROM no_show_counts WHERE user_id IN (SELECT id FROM users WHERE is_deleted = true)"))
    conn.execute(
        sa.text(
            """
            DELETE FROM banned_users
            WHERE user_id IN (SELECT id FROM users WHERE is_deleted = true)
               OR user_uuid IN (SELECT uuid FROM users WHERE is_deleted = true)
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM reservation_requests
            WHERE user_id IN (SELECT id FROM users WHERE is_deleted = true)
               OR user_uuid IN (SELECT uuid FROM users WHERE is_deleted = true)
            """
        )
    )
    conn.execute(sa.text("DELETE FROM reservations WHERE user_id IN (SELECT id FROM users WHERE is_deleted = true)"))
    conn.execute(sa.text("DELETE FROM users WHERE is_deleted = true"))
    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_deleted")


def downgrade() -> None:
    op.add_column("users", sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
