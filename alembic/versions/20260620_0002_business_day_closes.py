from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0002"
down_revision = "20260620_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "business_day_closes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.String(length=64), nullable=False),
        sa.Column("processed_reservations", sa.Integer(), nullable=False),
        sa.Column("visited_reservations", sa.Integer(), nullable=False),
        sa.Column("no_show_users", sa.Integer(), nullable=False),
        sa.Column("no_show_reservations", sa.Integer(), nullable=False),
        sa.Column("auto_banned_users", sa.Integer(), nullable=False),
        sa.UniqueConstraint("sale_date"),
    )
    op.create_index("ix_business_day_closes_sale_date", "business_day_closes", ["sale_date"])
    op.create_index("ix_business_day_closes_closed_at", "business_day_closes", ["closed_at"])


def downgrade() -> None:
    op.drop_table("business_day_closes")
