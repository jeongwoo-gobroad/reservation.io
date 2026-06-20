from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260620_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("kakao_id", sa.String(length=128), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kakao_id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_users_uuid", "users", ["uuid"])
    op.create_index("ix_users_kakao_id", "users", ["kakao_id"])

    op.create_table(
        "schedule_sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("can_bundle", sa.Boolean(), nullable=False),
        sa.Column("has_time_slots", sa.Boolean(), nullable=False),
        sa.Column("reservation_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_schedule_sets_uuid", "schedule_sets", ["uuid"])
    op.create_index("ix_schedule_sets_sale_date", "schedule_sets", ["sale_date"])
    op.create_index("ix_schedule_sets_sale_date_open", "schedule_sets", ["sale_date", "reservation_open"])

    op.create_table(
        "product_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.Integer(), sa.ForeignKey("schedule_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("purchase_limit", sa.Integer(), nullable=False),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_product_groups_uuid", "product_groups", ["uuid"])
    op.create_index("ix_product_groups_schedule_set_id", "product_groups", ["schedule_set_id"])

    op.create_table(
        "sale_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.Integer(), sa.ForeignKey("schedule_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("product_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("stock_quantity", sa.Integer(), nullable=True),
        sa.Column("per_user_limit", sa.Integer(), nullable=False),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_sale_products_uuid", "sale_products", ["uuid"])
    op.create_index("ix_sale_products_schedule_set_id", "sale_products", ["schedule_set_id"])
    op.create_index("ix_sale_products_group_id", "sale_products", ["group_id"])

    op.create_table(
        "time_slots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("schedule_set_id", sa.Integer(), sa.ForeignKey("schedule_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=32), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.UniqueConstraint("schedule_set_id", "code", name="uq_time_slot_schedule_code"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_time_slots_uuid", "time_slots", ["uuid"])
    op.create_index("ix_time_slots_schedule_set_id", "time_slots", ["schedule_set_id"])

    op.create_table(
        "reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("schedule_set_id", sa.Integer(), sa.ForeignKey("schedule_sets.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("sale_products.id"), nullable=False),
        sa.Column("time_slot_id", sa.Integer(), sa.ForeignKey("time_slots.id"), nullable=True),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_reservations_uuid", "reservations", ["uuid"])
    op.create_index("ix_reservations_user_id", "reservations", ["user_id"])
    op.create_index("ix_reservations_product_id", "reservations", ["product_id"])
    op.create_index("ix_reservations_time_slot_id", "reservations", ["time_slot_id"])
    op.create_index("ix_reservations_sale_date", "reservations", ["sale_date"])
    op.create_index("ix_reservations_user_date_status", "reservations", ["user_id", "sale_date", "status"])

    op.create_table(
        "completed_visits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column("reservation_id", sa.Integer(), sa.ForeignKey("reservations.id"), nullable=False),
        sa.Column("reservation_uuid", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("schedule_title", sa.String(length=160), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("time_slot_label", sa.String(length=32), nullable=False),
        sa.Column("no_show", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_completed_visits_uuid", "completed_visits", ["uuid"])
    op.create_index("ix_completed_visits_reservation_id", "completed_visits", ["reservation_id"])
    op.create_index("ix_completed_visits_reservation_uuid", "completed_visits", ["reservation_uuid"])
    op.create_index("ix_completed_visits_user_id", "completed_visits", ["user_id"])
    op.create_index("ix_completed_visits_user_uuid", "completed_visits", ["user_uuid"])
    op.create_index("ix_completed_visits_sale_date", "completed_visits", ["sale_date"])
    op.create_index("ix_completed_visits_completed_at", "completed_visits", ["completed_at"])

    op.create_table(
        "no_show_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_no_show_counts_user_id", "no_show_counts", ["user_id"])

    op.create_table(
        "banned_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_banned_users_user_id", "banned_users", ["user_id"])
    op.create_index("ix_banned_users_user_uuid", "banned_users", ["user_uuid"])

    op.create_table(
        "ip_bans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=240), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address"),
    )
    op.create_index("ix_ip_bans_ip_address", "ip_bans", ["ip_address"])

    op.create_table(
        "admin_login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ip_address", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ip_address"),
    )
    op.create_index("ix_admin_login_attempts_ip_address", "admin_login_attempts", ["ip_address"])

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=80), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("admin_login_attempts")
    op.drop_table("ip_bans")
    op.drop_table("banned_users")
    op.drop_table("no_show_counts")
    op.drop_table("completed_visits")
    op.drop_table("reservations")
    op.drop_table("time_slots")
    op.drop_table("sale_products")
    op.drop_table("product_groups")
    op.drop_table("schedule_sets")
    op.drop_table("users")

