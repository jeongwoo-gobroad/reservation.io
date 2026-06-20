from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0004"
down_revision = "20260620_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("ip_rate_limits")
    op.create_table(
        "ip_rate_limits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("actor_key", sa.String(length=96), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("route_key", sa.String(length=180), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address", "actor_key", "route_key", "window_started_at", name="uq_ip_rate_limits_scope_window"),
    )
    op.create_index("ix_ip_rate_limits_ip_address", "ip_rate_limits", ["ip_address"])
    op.create_index("ix_ip_rate_limits_actor_key", "ip_rate_limits", ["actor_key"])
    op.create_index("ix_ip_rate_limits_route_key", "ip_rate_limits", ["route_key"])
    op.create_index("ix_ip_rate_limits_window_started_at", "ip_rate_limits", ["window_started_at"])


def downgrade() -> None:
    op.drop_table("ip_rate_limits")
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
