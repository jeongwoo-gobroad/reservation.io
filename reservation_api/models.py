from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    kakao_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    reservations: Mapped[list[Reservation]] = relationship(back_populates="user")


class ScheduleSet(Base):
    __tablename__ = "schedule_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    sale_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(160))
    can_bundle: Mapped[bool] = mapped_column(Boolean, default=True)
    has_time_slots: Mapped[bool] = mapped_column(Boolean, default=False)
    reservation_open: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    groups: Mapped[list[ProductGroup]] = relationship(back_populates="schedule_set", cascade="all, delete-orphan")
    products: Mapped[list[SaleProduct]] = relationship(back_populates="schedule_set", cascade="all, delete-orphan")
    time_slots: Mapped[list[TimeSlot]] = relationship(back_populates="schedule_set", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_schedule_sets_sale_date_open", "sale_date", "reservation_open"),)


class ProductGroup(Base):
    __tablename__ = "product_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    schedule_set_id: Mapped[int] = mapped_column(ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    purchase_limit: Mapped[int] = mapped_column(Integer, default=1)

    schedule_set: Mapped[ScheduleSet] = relationship(back_populates="groups")
    products: Mapped[list[SaleProduct]] = relationship(back_populates="group", cascade="all, delete-orphan")


class SaleProduct(Base):
    __tablename__ = "sale_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    schedule_set_id: Mapped[int] = mapped_column(ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("product_groups.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    stock_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1)

    schedule_set: Mapped[ScheduleSet] = relationship(back_populates="products")
    group: Mapped[ProductGroup] = relationship(back_populates="products")
    reservations: Mapped[list[Reservation]] = relationship(back_populates="product")


class TimeSlot(Base):
    __tablename__ = "time_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    schedule_set_id: Mapped[int] = mapped_column(ForeignKey("schedule_sets.id", ondelete="CASCADE"), index=True)
    code: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(32))
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    schedule_set: Mapped[ScheduleSet] = relationship(back_populates="time_slots")

    __table_args__ = (UniqueConstraint("schedule_set_id", "code", name="uq_time_slot_schedule_code"),)


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    schedule_set_id: Mapped[int] = mapped_column(ForeignKey("schedule_sets.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("sale_products.id"), index=True)
    time_slot_id: Mapped[int | None] = mapped_column(ForeignKey("time_slots.id"), nullable=True, index=True)
    sale_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(24), default="reserved", index=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    user: Mapped[User] = relationship(back_populates="reservations")
    product: Mapped[SaleProduct] = relationship(back_populates="reservations")
    schedule_set: Mapped[ScheduleSet] = relationship()
    time_slot: Mapped[TimeSlot | None] = relationship()

    __table_args__ = (Index("ix_reservations_user_date_status", "user_id", "sale_date", "status"),)


class CompletedVisit(Base):
    __tablename__ = "completed_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=new_uuid)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), index=True)
    reservation_uuid: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_uuid: Mapped[str] = mapped_column(String(36), index=True)
    sale_date: Mapped[date] = mapped_column(Date, index=True)
    nickname: Mapped[str] = mapped_column(String(64))
    schedule_title: Mapped[str] = mapped_column(String(160))
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    time_slot_label: Mapped[str] = mapped_column(String(32), default="")
    no_show: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)


class BusinessDayClose(Base):
    __tablename__ = "business_day_closes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sale_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    closed_by: Mapped[str] = mapped_column(String(64), default="admin")
    processed_reservations: Mapped[int] = mapped_column(Integer, default=0)
    visited_reservations: Mapped[int] = mapped_column(Integer, default=0)
    no_show_users: Mapped[int] = mapped_column(Integer, default=0)
    no_show_reservations: Mapped[int] = mapped_column(Integer, default=0)
    auto_banned_users: Mapped[int] = mapped_column(Integer, default=0)


class NoShowCount(Base):
    __tablename__ = "no_show_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(64))
    count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class BannedUser(Base):
    __tablename__ = "banned_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    user_uuid: Mapped[str] = mapped_column(String(36), index=True)
    nickname: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(240), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class IpBan(Base):
    __tablename__ = "ip_bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(240), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class AdminLoginAttempt(Base):
    __tablename__ = "admin_login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
