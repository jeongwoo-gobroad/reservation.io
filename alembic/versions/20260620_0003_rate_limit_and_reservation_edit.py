from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0003"
down_revision = "20260620_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ip_bans", sa.Column("user_uuid", sa.String(length=36), nullable=False, server_default=""))
    op.add_column("ip_bans", sa.Column("nickname", sa.String(length=64), nullable=False, server_default=""))
    op.create_table(
        "ip_rate_limits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address"),
    )
    op.create_index("ix_ip_rate_limits_ip_address", "ip_rate_limits", ["ip_address"])


def downgrade() -> None:
    op.drop_table("ip_rate_limits")
    op.drop_column("ip_bans", "nickname")
    op.drop_column("ip_bans", "user_uuid")
