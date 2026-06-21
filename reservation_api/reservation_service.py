from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models
from .read_models import active_reservations_for_date, reserved_customers_for_slot, reserved_quantity
from .schemas import ReservationCreate, ReservationPatch


TERMINAL_REQUEST_STATUSES = {"success", "failed"}
PROCESSABLE_REQUEST_STATUSES = {"pending", "enqueued", "processing", "enqueue_failed"}


def model_payload(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def reservation_payload_json(body: ReservationCreate) -> str:
    return json.dumps(model_payload(body), ensure_ascii=False, separators=(",", ":"))


def mutation_payload_json(body: Any) -> str:
    if hasattr(body, "model_dump"):
        payload = body.model_dump(mode="json", exclude_unset=True, exclude={"idempotency_key"})
    else:
        payload = body.dict(exclude_unset=True, exclude={"idempotency_key"})
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def reservation_body_from_json(payload_json: str) -> ReservationCreate:
    return ReservationCreate(**json.loads(payload_json))


def reservation_patch_from_json(payload_json: str) -> ReservationPatch:
    return ReservationPatch(**json.loads(payload_json or "{}"))


def http_error_message(exc: HTTPException) -> str:
    if isinstance(exc.detail, str):
        return exc.detail
    return json.dumps(exc.detail, ensure_ascii=False)


def create_reservations_for_user_id(db: Session, body: ReservationCreate, user_id: int) -> list[models.Reservation]:
    if not body.items:
        raise HTTPException(status_code=400, detail="예약할 상품을 선택해 주세요.")
    user = db.scalar(select(models.User).where(models.User.id == user_id).with_for_update())
    if not user:
        raise HTTPException(status_code=401, detail="user not found")

    selected_set_uuids = {item.schedule_set_uuid for item in body.items}
    sets = db.scalars(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.uuid.in_(selected_set_uuids))
        .options(selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.id)
        .with_for_update()
    ).all()
    sets_by_uuid = {row.uuid: row for row in sets}
    if len(sets_by_uuid) != len(selected_set_uuids):
        raise HTTPException(status_code=400, detail="유효하지 않은 판매 일정이 포함되어 있습니다.")
    if any(row.sale_date != body.sale_date or not row.reservation_open for row in sets):
        raise HTTPException(status_code=400, detail="예약 가능한 일정이 아닙니다.")
    if len(selected_set_uuids) > 1 and any(not row.can_bundle for row in sets):
        blocked = ", ".join(row.title for row in sets if not row.can_bundle)
        raise HTTPException(status_code=400, detail=f"[{blocked}] 일정은 같은 날짜의 다른 일정과 함께 예약할 수 없습니다.")
    if _is_user_banned(db, user):
        raise HTTPException(status_code=403, detail="예약이 제한된 사용자입니다.")

    already_consumed = db.scalars(
        select(models.Reservation.schedule_set_id).where(
            models.Reservation.user_id == user.id,
            models.Reservation.schedule_set_id.in_([row.id for row in sets]),
            models.Reservation.status.in_(["visited", "no_show"]),
        )
    ).all()
    if already_consumed:
        consumed_ids = set(already_consumed)
        blocked_titles = ", ".join(row.title for row in sets if row.id in consumed_ids)
        raise HTTPException(status_code=400, detail=f"[{blocked_titles}] 일정은 이미 수령 완료 또는 노쇼 처리되어 다시 예약할 수 없습니다.")
    if active_reservations_for_date(db, user.id, body.sale_date):
        raise HTTPException(status_code=400, detail="이미 같은 날짜에 예약이 있습니다. 변경은 기존 예약 취소 후 다시 진행해 주세요.")

    products = db.scalars(
        select(models.SaleProduct)
        .where(models.SaleProduct.uuid.in_({item.product_uuid for item in body.items}))
        .options(selectinload(models.SaleProduct.group))
        .order_by(models.SaleProduct.id)
        .with_for_update()
    ).all()
    products_by_uuid = {row.uuid: row for row in products}
    time_slots = db.scalars(
        select(models.TimeSlot)
        .where(models.TimeSlot.uuid.in_({item.time_slot_uuid for item in body.items if item.time_slot_uuid}))
        .order_by(models.TimeSlot.id)
        .with_for_update()
    ).all()
    time_slots_by_uuid = {row.uuid: row for row in time_slots}

    reservations: list[models.Reservation] = []
    group_totals: dict[int, int] = defaultdict(int)
    product_totals: dict[int, int] = defaultdict(int)
    slot_users_seen: set[int] = set()
    for item in body.items:
        schedule_set = sets_by_uuid[item.schedule_set_uuid]
        product = products_by_uuid.get(item.product_uuid)
        if not product or product.schedule_set_id != schedule_set.id:
            raise HTTPException(status_code=400, detail="판매 일정에 맞지 않는 상품이 포함되어 있습니다.")
        product_totals[product.id] += item.quantity
        if product_totals[product.id] > product.per_user_limit:
            raise HTTPException(status_code=400, detail=f"[{product.name}] 상품은 1인당 {product.per_user_limit}개까지 예약할 수 있습니다.")
        group_totals[product.group_id] += item.quantity
        if group_totals[product.group_id] > product.group.purchase_limit:
            raise HTTPException(status_code=400, detail=f"[{product.group.name}] 그룹은 총 {product.group.purchase_limit}개까지만 선택 가능합니다.")
        if product.stock_quantity is not None and reserved_quantity(db, product.id) + product_totals[product.id] > product.stock_quantity:
            raise HTTPException(status_code=400, detail=f"[{product.name}] 상품의 재고가 부족합니다.")
        time_slot = None
        if schedule_set.has_time_slots:
            if not item.time_slot_uuid:
                raise HTTPException(status_code=400, detail=f"[{schedule_set.title}] 방문 시간을 선택해 주세요.")
            time_slot = time_slots_by_uuid.get(item.time_slot_uuid)
            if not time_slot or time_slot.schedule_set_id != schedule_set.id:
                raise HTTPException(status_code=400, detail="유효하지 않은 방문 시간입니다.")
            if time_slot.capacity is not None and time_slot.id not in slot_users_seen:
                if reserved_customers_for_slot(db, time_slot.id) >= time_slot.capacity:
                    raise HTTPException(status_code=400, detail=f"[{time_slot.label}] 방문 시간은 예약이 모두 찼습니다.")
                slot_users_seen.add(time_slot.id)
        reservation = models.Reservation(
            user_id=user.id,
            schedule_set_id=schedule_set.id,
            product_id=product.id,
            time_slot_id=time_slot.id if time_slot else None,
            sale_date=body.sale_date,
            quantity=item.quantity,
        )
        db.add(reservation)
        reservations.append(reservation)
    db.flush()
    return reservations


def update_reservation_for_user_id(db: Session, reservation_uuid: str, body: ReservationPatch, user_id: int) -> models.Reservation:
    provided_fields = getattr(body, "model_fields_set", getattr(body, "__fields_set__", set()))
    if not ({"quantity", "time_slot_uuid"} & set(provided_fields)):
        raise HTTPException(status_code=400, detail="수정할 수량 또는 방문 시간을 입력해 주세요.")

    locked_user = db.scalar(select(models.User).where(models.User.id == user_id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=401, detail="user not found")
    reservation = db.scalar(
        select(models.Reservation)
        .where(
            models.Reservation.uuid == reservation_uuid,
            models.Reservation.user_id == user_id,
            models.Reservation.status == "reserved",
        )
        .options(
            selectinload(models.Reservation.product).selectinload(models.SaleProduct.group),
            selectinload(models.Reservation.schedule_set),
            selectinload(models.Reservation.time_slot),
        )
        .with_for_update()
    )
    if not reservation:
        raise HTTPException(status_code=404, detail="수정 가능한 예약을 찾을 수 없습니다.")

    schedule_set = db.scalar(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.id == reservation.schedule_set_id)
        .options(selectinload(models.ScheduleSet.time_slots))
        .with_for_update()
    )
    product = db.scalar(
        select(models.SaleProduct)
        .where(models.SaleProduct.id == reservation.product_id)
        .options(selectinload(models.SaleProduct.group))
        .with_for_update()
    )
    if not product or not schedule_set:
        raise HTTPException(status_code=404, detail="예약 일정 또는 상품을 찾을 수 없습니다.")

    new_quantity = body.quantity if body.quantity is not None else reservation.quantity
    if new_quantity > product.per_user_limit:
        raise HTTPException(status_code=400, detail=f"[{product.name}] 상품은 1인당 {product.per_user_limit}개까지 예약할 수 있습니다.")

    existing_group_total = int(
        db.scalar(
            select(func.coalesce(func.sum(models.Reservation.quantity), 0))
            .join(models.SaleProduct, models.SaleProduct.id == models.Reservation.product_id)
            .where(
                models.Reservation.user_id == user_id,
                models.Reservation.schedule_set_id == reservation.schedule_set_id,
                models.Reservation.status == "reserved",
                models.Reservation.id != reservation.id,
                models.SaleProduct.group_id == product.group_id,
            )
        )
        or 0
    )
    if existing_group_total + new_quantity > product.group.purchase_limit:
        raise HTTPException(status_code=400, detail=f"[{product.group.name}] 그룹은 총 {product.group.purchase_limit}개까지 선택 가능합니다.")

    if product.stock_quantity is not None:
        next_reserved_quantity = reserved_quantity(db, product.id) - reservation.quantity + new_quantity
        if next_reserved_quantity > product.stock_quantity:
            raise HTTPException(status_code=400, detail=f"[{product.name}] 상품의 남은 재고가 부족합니다.")

    next_time_slot_id = reservation.time_slot_id
    if schedule_set.has_time_slots:
        if "time_slot_uuid" in provided_fields:
            if not body.time_slot_uuid:
                raise HTTPException(status_code=400, detail="방문 시간을 선택해 주세요.")
            next_slot = db.scalar(
                select(models.TimeSlot)
                .where(models.TimeSlot.uuid == body.time_slot_uuid, models.TimeSlot.schedule_set_id == schedule_set.id)
                .with_for_update()
            )
            if not next_slot:
                raise HTTPException(status_code=400, detail="유효하지 않은 방문 시간입니다.")
            if next_slot.id != reservation.time_slot_id and next_slot.capacity is not None:
                already_counted = bool(
                    db.scalar(
                        select(models.Reservation.id).where(
                            models.Reservation.time_slot_id == next_slot.id,
                            models.Reservation.user_id == user_id,
                            models.Reservation.status == "reserved",
                            models.Reservation.id != reservation.id,
                        )
                    )
                )
                if not already_counted and reserved_customers_for_slot(db, next_slot.id) >= next_slot.capacity:
                    raise HTTPException(status_code=400, detail=f"[{next_slot.label}] 방문 시간은 예약이 모두 찼습니다.")
            next_time_slot_id = next_slot.id
    elif "time_slot_uuid" in provided_fields and body.time_slot_uuid:
        raise HTTPException(status_code=400, detail="이 일정은 방문 시간을 나누어 받지 않습니다.")

    reservation.quantity = new_quantity
    reservation.time_slot_id = next_time_slot_id
    reservation.updated_at = models.now_utc()
    db.flush()
    return reservation


def cancel_reservation_for_user_id(db: Session, reservation_uuid: str, user_id: int) -> models.Reservation:
    locked_user = db.scalar(select(models.User).where(models.User.id == user_id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=401, detail="user not found")
    reservation = db.scalar(
        select(models.Reservation)
        .where(
            models.Reservation.uuid == reservation_uuid,
            models.Reservation.user_id == user_id,
            models.Reservation.status == "reserved",
        )
        .with_for_update()
    )
    if not reservation:
        raise HTTPException(status_code=404, detail="취소 가능한 예약을 찾을 수 없습니다.")
    reservation.status = "cancelled"
    reservation.updated_at = models.now_utc()
    db.flush()
    return reservation


def process_reservation_request(db: Session, request_uuid: str) -> models.ReservationRequest:
    request_row = db.scalar(
        select(models.ReservationRequest).where(models.ReservationRequest.uuid == request_uuid).with_for_update()
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="reservation request not found")
    if request_row.status in TERMINAL_REQUEST_STATUSES:
        return request_row
    if request_row.status not in PROCESSABLE_REQUEST_STATUSES:
        raise HTTPException(status_code=409, detail=f"reservation request cannot be processed from {request_row.status}")

    request_row.status = "processing"
    request_row.attempts += 1
    request_row.updated_at = models.now_utc()
    action = request_row.action or "create"
    try:
        if action == "create":
            body = reservation_body_from_json(request_row.payload_json)
            reservations = create_reservations_for_user_id(db, body, request_row.user_id)
        elif action == "update":
            if not request_row.target_reservation_uuid:
                raise HTTPException(status_code=400, detail="수정할 예약 번호가 없습니다.")
            reservation = update_reservation_for_user_id(
                db,
                request_row.target_reservation_uuid,
                reservation_patch_from_json(request_row.payload_json),
                request_row.user_id,
            )
            reservations = [reservation]
        elif action == "cancel":
            if not request_row.target_reservation_uuid:
                raise HTTPException(status_code=400, detail="취소할 예약 번호가 없습니다.")
            reservations = [cancel_reservation_for_user_id(db, request_row.target_reservation_uuid, request_row.user_id)]
        else:
            raise HTTPException(status_code=400, detail=f"지원하지 않는 예약 요청 종류입니다: {action}")
    except HTTPException as exc:
        request_row.status = "failed"
        request_row.error_message = http_error_message(exc)
        request_row.finished_at = models.now_utc()
        request_row.updated_at = request_row.finished_at
        db.commit()
        return request_row

    request_row.status = "success"
    request_row.error_message = ""
    request_row.result_json = json.dumps({"reservation_uuids": [row.uuid for row in reservations]}, separators=(",", ":"))
    request_row.finished_at = models.now_utc()
    request_row.updated_at = request_row.finished_at
    db.commit()
    return request_row


def _is_user_banned(db: Session, user: models.User) -> bool:
    return bool(
        db.scalar(
            select(models.BannedUser).where(
                models.BannedUser.user_uuid == user.uuid,
                models.BannedUser.active.is_(True),
            )
        )
    )
