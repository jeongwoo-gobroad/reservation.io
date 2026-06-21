from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260621_0007"
down_revision = "20260621_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservation_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("task_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_reservation_requests_user_idempotency"),
    )
    op.create_index("ix_reservation_requests_uuid", "reservation_requests", ["uuid"], unique=True)
    op.create_index("ix_reservation_requests_user_id", "reservation_requests", ["user_id"])
    op.create_index("ix_reservation_requests_user_uuid", "reservation_requests", ["user_uuid"])
    op.create_index("ix_reservation_requests_sale_date", "reservation_requests", ["sale_date"])
    op.create_index("ix_reservation_requests_status", "reservation_requests", ["status"])
    op.create_index("ix_reservation_requests_user_status", "reservation_requests", ["user_id", "status"])
    op.create_index("ix_reservation_requests_status_created", "reservation_requests", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_reservation_requests_status_created", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_user_status", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_status", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_sale_date", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_user_uuid", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_user_id", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_uuid", table_name="reservation_requests")
    op.drop_table("reservation_requests")
