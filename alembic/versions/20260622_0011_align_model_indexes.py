"""align database indexes with current SQLAlchemy models

Revision ID: 20260622_0011
Revises: 20260621_0010
Create Date: 2026-06-22
"""

from __future__ import annotations

from alembic import op


revision = "20260622_0011"
down_revision = "20260621_0010"
branch_labels = None
depends_on = None


UNIQUE_INDEXES = [
    ("business_day_closes", "ix_business_day_closes_sale_date", ["sale_date"]),
    ("completed_visits", "ix_completed_visits_uuid", ["uuid"]),
    ("no_show_counts", "ix_no_show_counts_user_id", ["user_id"]),
    ("product_groups", "ix_product_groups_uuid", ["uuid"]),
    ("reservations", "ix_reservations_uuid", ["uuid"]),
    ("sale_products", "ix_sale_products_uuid", ["uuid"]),
    ("schedule_sets", "ix_schedule_sets_uuid", ["uuid"]),
    ("time_slots", "ix_time_slots_uuid", ["uuid"]),
    ("users", "ix_users_kakao_id", ["kakao_id"]),
    ("users", "ix_users_uuid", ["uuid"]),
]


def upgrade() -> None:
    for table_name, index_name, columns in UNIQUE_INDEXES:
        op.drop_index(index_name, table_name=table_name)
        op.create_index(index_name, table_name, columns, unique=True)

    op.create_index("ix_reservations_schedule_set_id", "reservations", ["schedule_set_id"])
    op.create_index("ix_reservations_status", "reservations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reservations_status", table_name="reservations")
    op.drop_index("ix_reservations_schedule_set_id", table_name="reservations")

    for table_name, index_name, columns in reversed(UNIQUE_INDEXES):
        op.drop_index(index_name, table_name=table_name)
        op.create_index(index_name, table_name, columns)
