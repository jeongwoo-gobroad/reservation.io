from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models
from .schemas import CompletedVisitOut, PendingDayCloseOut, ReservationOut, ReservationRequestOut, ScheduleSetOut


def schedule_out(
    db: Session,
    row: models.ScheduleSet,
    include_remaining: bool = False,
    respect_visibility: bool = True,
) -> ScheduleSetOut:
    show_stock_remaining = getattr(row, "show_stock_remaining", True)
    show_time_slot_remaining = getattr(row, "show_time_slot_remaining", True)
    include_stock_remaining = include_remaining and (show_stock_remaining or not respect_visibility)
    include_time_slot_remaining = include_remaining and (show_time_slot_remaining or not respect_visibility)
    return ScheduleSetOut(
        uuid=row.uuid,
        sale_date=row.sale_date,
        title=row.title,
        can_bundle=row.can_bundle,
        has_time_slots=row.has_time_slots,
        reservation_open=row.reservation_open,
        show_stock_remaining=show_stock_remaining,
        show_time_slot_remaining=show_time_slot_remaining,
        products=[
            {
                "uuid": product.uuid,
                "group_uuid": product.group.uuid,
                "group_name": product.group.name,
                "group_purchase_limit": product.group.purchase_limit,
                "name": product.name,
                "stock_quantity": product.stock_quantity if show_stock_remaining or not respect_visibility else None,
                "stock_remaining": stock_remaining(db, product) if include_stock_remaining else None,
                "per_user_limit": product.per_user_limit,
            }
            for product in row.products
        ],
        time_slots=[
            {
                "uuid": slot.uuid,
                "code": slot.code,
                "label": slot.label,
                "capacity": slot.capacity if show_time_slot_remaining or not respect_visibility else None,
                "remaining_customers": time_slot_remaining(db, slot) if include_time_slot_remaining else None,
            }
            for slot in sorted(row.time_slots, key=lambda item: item.code)
        ],
    )


def reservation_out(row: models.Reservation) -> ReservationOut:
    user = getattr(row, "user", None)
    nickname = user.nickname if user else ""
    user_uuid = user.uuid if user else ""
    return ReservationOut(
        uuid=row.uuid,
        user_uuid=user_uuid,
        nickname=nickname,
        customer_name=nickname,
        sale_date=row.sale_date,
        schedule_set_uuid=row.schedule_set.uuid,
        schedule_title=row.schedule_set.title,
        product_uuid=row.product.uuid,
        product_name=row.product.name,
        quantity=row.quantity,
        time_slot_uuid=row.time_slot.uuid if row.time_slot else None,
        time_slot_label=row.time_slot.label if row.time_slot else "",
        status=row.status,
        reserved_at=row.reserved_at,
    )


def reservation_request_out(db: Session, row: models.ReservationRequest) -> ReservationRequestOut:
    reservations: list[ReservationOut] = []
    reservation_uuids = _reservation_request_result_uuids(row)
    if reservation_uuids:
        reservation_rows = db.scalars(
            select(models.Reservation)
            .where(models.Reservation.uuid.in_(reservation_uuids))
            .options(
                selectinload(models.Reservation.user),
                selectinload(models.Reservation.product),
                selectinload(models.Reservation.schedule_set),
                selectinload(models.Reservation.time_slot),
            )
        ).all()
        rows_by_uuid = {reservation.uuid: reservation for reservation in reservation_rows}
        reservations = [reservation_out(rows_by_uuid[uuid]) for uuid in reservation_uuids if uuid in rows_by_uuid]
    return ReservationRequestOut(
        request_uuid=row.uuid,
        action=row.action,
        status=row.status,
        sale_date=row.sale_date,
        target_reservation_uuid=row.target_reservation_uuid,
        idempotency_key=row.idempotency_key,
        error_message=row.error_message,
        reservations=reservations,
        attempts=row.attempts,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def completed_row(row: models.Reservation, no_show: bool) -> models.CompletedVisit:
    return models.CompletedVisit(
        reservation_id=row.id,
        reservation_uuid=row.uuid,
        user_id=row.user_id,
        user_uuid=row.user.uuid,
        sale_date=row.sale_date,
        nickname=row.user.nickname,
        schedule_title=row.schedule_set.title,
        product_name=row.product.name,
        quantity=row.quantity,
        time_slot_label=row.time_slot.label if row.time_slot else "",
        no_show=no_show,
    )


def completed_out(row: models.CompletedVisit) -> CompletedVisitOut:
    return CompletedVisitOut(
        uuid=row.uuid,
        reservation_uuid=row.reservation_uuid,
        user_uuid=row.user_uuid,
        nickname=row.nickname,
        sale_date=row.sale_date,
        schedule_title=row.schedule_title,
        product_name=row.product_name,
        quantity=row.quantity,
        time_slot_label=row.time_slot_label,
        no_show=row.no_show,
        completed_at=row.completed_at,
    )


def pending_day_close_out(db: Session, sale_date: date) -> PendingDayCloseOut:
    reserved_rows = db.scalars(
        select(models.Reservation.user_id).where(
            models.Reservation.sale_date == sale_date,
            models.Reservation.status == "reserved",
        )
    ).all()
    return PendingDayCloseOut(
        sale_date=sale_date,
        schedule_count=int(
            db.scalar(select(func.count(models.ScheduleSet.id)).where(models.ScheduleSet.sale_date == sale_date))
            or 0
        ),
        reserved_reservations=len(reserved_rows),
        visited_reservations=reservation_count(db, sale_date, "visited"),
        no_show_reservations=reservation_count(db, sale_date, "no_show"),
        cancelled_reservations=reservation_count(db, sale_date, "cancelled"),
        expected_no_show_users=len(set(reserved_rows)),
    )


def active_reservations_for_date(db: Session, user_id: int, sale_date: date) -> list[models.Reservation]:
    return list(
        db.scalars(
            select(models.Reservation).where(
                models.Reservation.user_id == user_id,
                models.Reservation.sale_date == sale_date,
                models.Reservation.status == "reserved",
            )
        ).all()
    )


def reservation_count(db: Session, sale_date: date, status_value: str) -> int:
    return int(
        db.scalar(
            select(func.count(models.Reservation.id)).where(
                models.Reservation.sale_date == sale_date,
                models.Reservation.status == status_value,
            )
        )
        or 0
    )


def reserved_quantity(db: Session, product_id: int) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(models.Reservation.quantity), 0)).where(
                models.Reservation.product_id == product_id,
                models.Reservation.status == "reserved",
            )
        )
        or 0
    )


def stock_remaining(db: Session, product: models.SaleProduct) -> int | None:
    if product.stock_quantity is None:
        return None
    return max(0, product.stock_quantity - reserved_quantity(db, product.id))


def reserved_customers_for_slot(db: Session, time_slot_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(func.distinct(models.Reservation.user_id))).where(
                models.Reservation.time_slot_id == time_slot_id,
                models.Reservation.status == "reserved",
            )
        )
        or 0
    )


def time_slot_remaining(db: Session, slot: models.TimeSlot) -> int | None:
    if slot.capacity is None:
        return None
    return max(0, slot.capacity - reserved_customers_for_slot(db, slot.id))


def is_user_banned(db: Session, user: models.User) -> bool:
    return bool(
        db.scalar(
            select(models.BannedUser).where(
                models.BannedUser.user_uuid == user.uuid,
                models.BannedUser.active.is_(True),
            )
        )
    )


def setting_value(db: Session, key: str, default: str) -> str:
    setting = db.get(models.AppSetting, key)
    return setting.value if setting else default


def _reservation_request_result_uuids(row: models.ReservationRequest) -> list[str]:
    if not row.result_json:
        return []
    try:
        import json

        payload = json.loads(row.result_json)
    except (TypeError, ValueError):
        return []
    values = payload.get("reservation_uuids", [])
    if not isinstance(values, list):
        return []
    return [str(value) for value in values]
