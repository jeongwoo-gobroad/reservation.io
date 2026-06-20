from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0005"
down_revision = "20260620_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ip_bans_new",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("actor_key", sa.String(length=96), nullable=False, server_default=""),
        sa.Column("user_uuid", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("nickname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address", "actor_key", name="uq_ip_bans_ip_actor"),
    )
    op.execute(
        """
        INSERT INTO ip_bans_new (id, ip_address, actor_key, user_uuid, nickname, reason, active, created_at, updated_at)
        SELECT id, ip_address, '', user_uuid, nickname, reason, active, created_at, updated_at
        FROM ip_bans
        """
    )
    op.drop_table("ip_bans")
    op.rename_table("ip_bans_new", "ip_bans")
    op.create_index("ix_ip_bans_ip_address", "ip_bans", ["ip_address"])
    op.create_index("ix_ip_bans_actor_key", "ip_bans", ["actor_key"])


def downgrade() -> None:
    op.create_table(
        "ip_bans_old",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("nickname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address"),
    )
    op.execute(
        """
        INSERT INTO ip_bans_old (id, ip_address, user_uuid, nickname, reason, active, created_at, updated_at)
        SELECT MIN(id), ip_address, MAX(user_uuid), MAX(nickname), MAX(reason), MAX(active), MIN(created_at), MAX(updated_at)
        FROM ip_bans
        GROUP BY ip_address
        """
    )
    op.drop_table("ip_bans")
    op.rename_table("ip_bans_old", "ip_bans")
    op.create_index("ix_ip_bans_ip_address", "ip_bans", ["ip_address"])
