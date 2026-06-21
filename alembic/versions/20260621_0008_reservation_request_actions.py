from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260621_0008"
down_revision = "20260621_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_requests",
        sa.Column("action", sa.String(length=16), nullable=False, server_default="create"),
    )
    op.add_column(
        "reservation_requests",
        sa.Column("target_reservation_uuid", sa.String(length=36), nullable=False, server_default=""),
    )
    op.create_index("ix_reservation_requests_action", "reservation_requests", ["action"])
    op.create_index("ix_reservation_requests_target_reservation_uuid", "reservation_requests", ["target_reservation_uuid"])
    op.create_index(
        "ix_reservation_requests_target_status",
        "reservation_requests",
        ["target_reservation_uuid", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_reservation_requests_target_status", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_target_reservation_uuid", table_name="reservation_requests")
    op.drop_index("ix_reservation_requests_action", table_name="reservation_requests")
    op.drop_column("reservation_requests", "target_reservation_uuid")
    op.drop_column("reservation_requests", "action")
