from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import models
from .config import Settings, load_settings
from .database import Base, engine, get_db
from .database import SessionLocal
from .message_bus import get_message_bus
from .otp import manual_code, qr_hash, remaining_seconds, verify_token
from .schemas import (
    AdminLoginRequest,
    AdminScanRequest,
    BannedUserIn,
    BannedUserOut,
    BlockStatusOut,
    CompleteVisitRequest,
    CompletedVisitOut,
    DayCloseOut,
    DayCloseRequest,
    IpBanIn,
    IpBanOut,
    KakaoCodeExchangeRequest,
    NicknameRequest,
    PendingDayCloseOut,
    ReservationCreate,
    ReservationOut,
    ReservationPatch,
    ScheduleSetCreate,
    ScheduleSetOut,
    ScheduleSetPatch,
    SettingOut,
    SettingUpdate,
    TokenResponse,
    UserOut,
    UserTokenResponse,
    VisitScanOut,
    VisitTokenOut,
)
from .security import bearer_token, client_ip, create_access_token, reject_banned_ip, require_admin, require_user, verify_access_token
from .timezone import local_today


settings = load_settings()
message_bus = get_message_bus(settings)
BLOCKED_USER_MESSAGE_KEY = "blocked_user_message_markdown"
DEFAULT_BLOCKED_USER_MESSAGE = "현재 접속이 제한되어 있습니다. 매장 관리자에게 문의해 주세요."
RATE_LIMIT_SKIP_PATHS = {"/health", "/users/me/block-status", f"/settings/{BLOCKED_USER_MESSAGE_KEY}"}
RATE_LIMIT_SKIP_PREFIXES = ("/swagger", "/redoc", "/openapi.json")
IP_BAN_READABLE_PATHS = {"/health", "/auth/kakao/exchange", "/users/me/block-status", f"/settings/{BLOCKED_USER_MESSAGE_KEY}"}
UUID_PATH_PART = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")

app = FastAPI(
    title="Pokecard Reservation API",
    version="2.0.0",
    description=(
        "Cloud Run serverless API for Pokemon card reservation, pickup verification, "
        "admin management, TOTP visit tokens, and Cloud SQL persistence."
    ),
    docs_url="/swagger" if settings.is_dev_mode else None,
    redoc_url="/redoc" if settings.is_dev_mode else None,
    openapi_url="/openapi.json" if settings.is_dev_mode else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def anti_macro_and_ip_ban_middleware(request: Request, call_next):
    if request.method == "OPTIONS" or should_skip_rate_limit(request.url.path):
        return await call_next(request)
    db = SessionLocal()
    try:
        ip_address = client_ip(request)
        actor_key, _, _ = rate_limit_actor(db, request)
        ban = active_ip_ban(db, ip_address, actor_key)
        if ban and request.url.path not in IP_BAN_READABLE_PATHS:
            return ip_ban_response(request, db, ban)
        triggered_ban = apply_anti_macro_limit(db, request, ip_address)
        if triggered_ban:
            return ip_ban_response(request, db, triggered_ban)
    finally:
        db.close()
    return await call_next(request)


@app.on_event("startup")
def create_local_dev_schema() -> None:
    if settings.is_dev_mode and settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "environment": settings.environment}


@app.post("/admin/login", response_model=TokenResponse, tags=["admin-auth"])
def admin_login(request: Request, body: AdminLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    reject_banned_ip(request, db)
    ip_address = client_ip(request)
    ok = body.admin_id == settings.admin_id and body.password == settings.admin_password
    attempt = db.scalar(select(models.AdminLoginAttempt).where(models.AdminLoginAttempt.ip_address == ip_address))
    if not ok:
        if not attempt:
            attempt = models.AdminLoginAttempt(ip_address=ip_address)
            db.add(attempt)
        attempt.failure_count = (attempt.failure_count or 0) + 1
        attempt.last_failed_at = models.now_utc()
        if attempt.failure_count >= settings.admin_login_fail_limit:
            ban = db.scalar(select(models.IpBan).where(models.IpBan.ip_address == ip_address, models.IpBan.actor_key == ""))
            if not ban:
                db.add(models.IpBan(ip_address=ip_address, reason="관리자 로그인 5회 이상 실패", active=True))
            else:
                ban.active = True
                ban.reason = "관리자 로그인 5회 이상 실패"
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin credentials")

    if attempt:
        attempt.failure_count = 0
        attempt.last_failed_at = None
    db.commit()
    return TokenResponse(access_token=create_access_token("admin", "admin", settings), expires_in_seconds=43200)


@app.post("/auth/kakao/exchange", response_model=UserTokenResponse, tags=["user-auth"])
def kakao_exchange(body: KakaoCodeExchangeRequest, db: Session = Depends(get_db)) -> UserTokenResponse:
    if not settings.kakao_rest_api_key:
        raise HTTPException(status_code=500, detail="KAKAO_REST_API_KEY is not configured")
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": settings.kakao_rest_api_key,
        "redirect_uri": body.redirect_uri,
        "code": body.code,
    }
    if settings.kakao_client_secret:
        token_payload["client_secret"] = settings.kakao_client_secret
    token_request = urllib.request.Request(
        settings.kakao_token_url,
        data=urllib.parse.urlencode(token_payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(token_request, timeout=12) as response:
            token_data = json.loads(response.read().decode("utf-8"))
        user_request = urllib.request.Request(
            settings.kakao_userinfo_url,
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
            method="GET",
        )
        with urllib.request.urlopen(user_request, timeout=12) as response:
            user_data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"kakao exchange failed: {exc}") from exc

    kakao_id = str(user_data["id"])
    user = db.scalar(select(models.User).where(models.User.kakao_id == kakao_id))
    if not user:
        user = models.User(kakao_id=kakao_id)
        db.add(user)
        db.flush()
    user.last_login_at = models.now_utc()
    db.commit()
    db.refresh(user)
    return UserTokenResponse(
        access_token=create_access_token(user.uuid, "user", settings),
        expires_in_seconds=43200,
        user_uuid=user.uuid,
        kakao_id=user.kakao_id,
        nickname=user.nickname,
    )


@app.get("/users/me", response_model=UserOut, tags=["users"])
def get_me(user: models.User = Depends(require_user)) -> models.User:
    return user


@app.get("/users/me/block-status", response_model=BlockStatusOut, tags=["users"])
def get_my_block_status(
    request: Request,
    token: str = Depends(bearer_token),
    db: Session = Depends(get_db),
) -> BlockStatusOut:
    payload = verify_access_token(token, settings)
    if payload.get("scope") != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="user token required")
    user = db.scalar(select(models.User).where(models.User.uuid == payload.get("sub")))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    actor_key = f"user:{user.uuid}"
    ban = active_ip_ban(db, client_ip(request), actor_key)
    return BlockStatusOut(
        blocked=bool(ban),
        ip_address=client_ip(request),
        actor_key=actor_key,
        user_uuid=user.uuid,
        nickname=user.nickname,
        reason=ban.reason if ban else "",
        message_markdown=setting_value(db, BLOCKED_USER_MESSAGE_KEY, DEFAULT_BLOCKED_USER_MESSAGE) if ban else "",
    )


@app.patch("/users/me/nickname", response_model=UserOut, tags=["users"])
def update_nickname(body: NicknameRequest, user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> models.User:
    user.nickname = body.nickname.strip()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/schedules/open", response_model=list[ScheduleSetOut], tags=["schedules"])
def public_open_schedules(db: Session = Depends(get_db)) -> list[ScheduleSetOut]:
    rows = db.scalars(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.reservation_open.is_(True), models.ScheduleSet.sale_date >= local_today(settings))
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.sale_date, models.ScheduleSet.title)
    ).all()
    return [schedule_out(db, row, include_remaining=True) for row in rows]


@app.get("/admin/schedules", response_model=list[ScheduleSetOut], tags=["admin-schedules"])
def admin_list_schedules(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> list[ScheduleSetOut]:
    rows = db.scalars(
        select(models.ScheduleSet)
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.sale_date, models.ScheduleSet.title)
    ).all()
    return [schedule_out(db, row, include_remaining=True) for row in rows]


@app.post("/admin/schedules", response_model=ScheduleSetOut, status_code=201, tags=["admin-schedules"])
def create_schedule_set(
    body: ScheduleSetCreate,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> ScheduleSetOut:
    schedule_set = models.ScheduleSet(
        sale_date=body.sale_date,
        title=body.title.strip(),
        can_bundle=body.can_bundle,
        has_time_slots=body.has_time_slots,
        reservation_open=body.reservation_open,
    )
    db.add(schedule_set)
    db.flush()
    for index, slot in enumerate(body.time_slots):
        db.add(models.TimeSlot(schedule_set_id=schedule_set.id, code=index, label=slot.label, capacity=slot.capacity))
    for group_in in body.groups:
        group = models.ProductGroup(
            schedule_set_id=schedule_set.id,
            name=group_in.name.strip(),
            purchase_limit=group_in.purchase_limit,
        )
        db.add(group)
        db.flush()
        for product_in in group_in.products:
            db.add(
                models.SaleProduct(
                    schedule_set_id=schedule_set.id,
                    group_id=group.id,
                    name=product_in.name.strip(),
                    stock_quantity=product_in.stock_quantity,
                    per_user_limit=product_in.per_user_limit,
                )
            )
    db.commit()
    db.refresh(schedule_set)
    return schedule_out(db, schedule_set, include_remaining=True)


@app.patch("/admin/schedules/{schedule_set_uuid}", response_model=ScheduleSetOut, tags=["admin-schedules"])
def patch_schedule_set(
    schedule_set_uuid: str,
    body: ScheduleSetPatch,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> ScheduleSetOut:
    schedule_set = require_schedule_set(db, schedule_set_uuid)
    if body.title is not None:
        schedule_set.title = body.title.strip()
    if body.can_bundle is not None:
        schedule_set.can_bundle = body.can_bundle
    if body.reservation_open is not None:
        schedule_set.reservation_open = body.reservation_open
    db.commit()
    db.refresh(schedule_set)
    return schedule_out(db, schedule_set, include_remaining=True)


@app.delete("/admin/schedules/{schedule_set_uuid}", status_code=204, tags=["admin-schedules"])
def delete_schedule_set(schedule_set_uuid: str, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> None:
    schedule_set = require_schedule_set(db, schedule_set_uuid)
    db.delete(schedule_set)
    db.commit()


@app.post("/admin/schedule-dates/{sale_date}/open", response_model=list[ScheduleSetOut], tags=["admin-schedules"])
def open_schedule_date(sale_date: date, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> list[ScheduleSetOut]:
    rows = db.scalars(select(models.ScheduleSet).where(models.ScheduleSet.sale_date == sale_date)).all()
    for row in rows:
        row.reservation_open = True
    db.commit()
    return [schedule_out(db, row, include_remaining=True) for row in rows]


@app.post("/admin/schedule-dates/{sale_date}/close", response_model=list[ScheduleSetOut], tags=["admin-schedules"])
def close_schedule_date(sale_date: date, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> list[ScheduleSetOut]:
    rows = db.scalars(select(models.ScheduleSet).where(models.ScheduleSet.sale_date == sale_date)).all()
    for row in rows:
        row.reservation_open = False
    db.commit()
    return [schedule_out(db, row, include_remaining=True) for row in rows]


@app.post("/admin/schedule-dates/{sale_date}/reset-reservations", tags=["admin-schedules"])
def reset_reservations_for_date(sale_date: date, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> dict[str, int]:
    rows = db.scalars(
        select(models.Reservation).where(models.Reservation.sale_date == sale_date, models.Reservation.status == "reserved")
    ).all()
    for row in rows:
        row.status = "cancelled"
    db.commit()
    return {"cancelled": len(rows)}


@app.get("/admin/reservations", response_model=list[ReservationOut], tags=["admin-reservations"])
def admin_reservations(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
    sale_date: date | None = None,
    nickname: str | None = None,
) -> list[ReservationOut]:
    stmt = (
        select(models.Reservation)
        .where(models.Reservation.status == "reserved")
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .order_by(models.Reservation.sale_date, models.Reservation.reserved_at)
    )
    if sale_date:
        stmt = stmt.where(models.Reservation.sale_date == sale_date)
    if nickname:
        stmt = stmt.join(models.User).where(models.User.nickname.contains(nickname.strip()))
    rows = db.scalars(stmt).all()
    return [reservation_out(row) for row in rows]


@app.post("/reservations", response_model=list[ReservationOut], status_code=201, tags=["reservations"])
def create_reservation(
    body: ReservationCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[ReservationOut]:
    if not body.items:
        raise HTTPException(status_code=400, detail="예약할 상품을 선택해 주세요.")
    locked_user = db.scalar(select(models.User).where(models.User.id == user.id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=401, detail="user not found")
    user = locked_user
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
    if is_user_banned(db, user):
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
        raise HTTPException(status_code=400, detail="이미 같은 날짜의 예약이 있습니다. 변경은 기존 예약 취소 후 다시 진행해 주세요.")

    products = db.scalars(
        select(models.SaleProduct)
        .where(models.SaleProduct.uuid.in_({item.product_uuid for item in body.items}))
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
    for item in body.items:
        schedule_set = sets_by_uuid[item.schedule_set_uuid]
        product = products_by_uuid.get(item.product_uuid)
        if not product or product.schedule_set_id != schedule_set.id:
            raise HTTPException(status_code=400, detail="판매 일정에 속하지 않은 상품이 포함되어 있습니다.")
        if item.quantity > product.per_user_limit:
            raise HTTPException(status_code=400, detail=f"[{product.name}] 상품은 1인당 {product.per_user_limit}개까지 예약할 수 있습니다.")
        group_totals[product.group_id] += item.quantity
        if group_totals[product.group_id] > product.group.purchase_limit:
            raise HTTPException(status_code=400, detail=f"[{product.group.name}] 그룹은 총 {product.group.purchase_limit}개까지만 선택 가능합니다.")
        if product.stock_quantity is not None and reserved_quantity(db, product.id) + item.quantity > product.stock_quantity:
            raise HTTPException(status_code=400, detail=f"[{product.name}] 상품의 재고가 부족합니다.")
        time_slot = None
        if schedule_set.has_time_slots:
            if not item.time_slot_uuid:
                raise HTTPException(status_code=400, detail=f"[{schedule_set.title}] 방문 시간을 선택해 주세요.")
            time_slot = time_slots_by_uuid.get(item.time_slot_uuid)
            if not time_slot or time_slot.schedule_set_id != schedule_set.id:
                raise HTTPException(status_code=400, detail="유효하지 않은 방문 시간입니다.")
            if time_slot.capacity is not None and reserved_customers_for_slot(db, time_slot.id) >= time_slot.capacity:
                raise HTTPException(status_code=400, detail=f"[{time_slot.label}] 방문 시간대 예약이 모두 찼습니다.")
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
    db.commit()
    for reservation in reservations:
        db.refresh(reservation)
    return [reservation_out(row) for row in reservations]


@app.get("/users/me/reservations", response_model=list[ReservationOut], tags=["reservations"])
def my_reservations(user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> list[ReservationOut]:
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.user_id == user.id, models.Reservation.status == "reserved")
        .options(selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .order_by(models.Reservation.sale_date, models.Reservation.reserved_at)
    ).all()
    return [reservation_out(row) for row in rows]


@app.patch("/reservations/{reservation_uuid}", response_model=ReservationOut, tags=["reservations"])
def update_reservation(
    reservation_uuid: str,
    body: ReservationPatch,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReservationOut:
    locked_user = db.scalar(select(models.User).where(models.User.id == user.id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=401, detail="user not found")
    reservation = db.scalar(
        select(models.Reservation)
        .where(
            models.Reservation.uuid == reservation_uuid,
            models.Reservation.user_id == user.id,
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
                models.Reservation.user_id == user.id,
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

    provided_fields = getattr(body, "model_fields_set", getattr(body, "__fields_set__", set()))
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
                            models.Reservation.user_id == user.id,
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
    db.commit()
    updated = db.scalar(
        select(models.Reservation)
        .where(models.Reservation.id == reservation.id)
        .options(selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
    )
    if not updated:
        raise HTTPException(status_code=404, detail="수정된 예약을 찾을 수 없습니다.")
    return reservation_out(updated)


@app.delete("/reservations/{reservation_uuid}", status_code=204, tags=["reservations"])
def cancel_reservation(reservation_uuid: str, user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> None:
    locked_user = db.scalar(select(models.User).where(models.User.id == user.id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=401, detail="user not found")
    reservation = db.scalar(
        select(models.Reservation)
        .where(
            models.Reservation.uuid == reservation_uuid,
            models.Reservation.user_id == user.id,
            models.Reservation.status == "reserved",
        )
        .with_for_update()
    )
    if not reservation:
        raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다.")
    reservation.status = "cancelled"
    db.commit()


@app.get("/users/me/visit-tokens", response_model=list[VisitTokenOut], tags=["visit"])
def my_visit_tokens(user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> list[VisitTokenOut]:
    today = local_today(settings)
    rows = db.scalars(
        select(models.Reservation).where(
            models.Reservation.user_id == user.id,
            models.Reservation.sale_date == today,
            models.Reservation.status == "reserved",
        )
    ).all()
    window = f"t-{settings.otp_delta},t,t+{settings.otp_delta}" if settings.otp_delta else "t"
    return [
        VisitTokenOut(
            reservation_uuid=row.uuid,
            qr_hash=qr_hash(user.uuid, row.uuid, settings),
            manual_code=manual_code(user.uuid, row.uuid, settings),
            expires_in_seconds=remaining_seconds(settings),
            hash_window=window,
        )
        for row in rows
    ]


@app.post("/admin/visits/scan", response_model=VisitScanOut, tags=["admin-visit"])
def scan_visit_token(
    body: AdminScanRequest,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> VisitScanOut:
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.status == "reserved", models.Reservation.sale_date == local_today(settings))
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
    ).all()
    for reservation in rows:
        if verify_token(body.token, reservation.user.uuid, reservation.uuid, settings, body.token_type):
            return VisitScanOut(
                user_uuid=reservation.user.uuid,
                nickname=reservation.user.nickname,
                reservation=reservation_out(reservation),
            )
    raise HTTPException(status_code=400, detail="QR 또는 수동 코드가 만료되었거나 올바르지 않습니다.")


@app.post("/admin/visits/complete", response_model=list[CompletedVisitOut], tags=["admin-visit"])
def complete_visit(
    body: CompleteVisitRequest,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> list[CompletedVisitOut]:
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.uuid.in_(body.reservation_uuids), models.Reservation.status == "reserved")
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .with_for_update()
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="처리할 예약이 없습니다.")
    completed = []
    for row in rows:
        row.status = "visited"
        visit = completed_row(row, no_show=False)
        db.add(visit)
        completed.append(visit)
    db.commit()
    for visit in completed:
        db.refresh(visit)
        message_bus.publish("visit.completed", {"completed_visit_uuid": visit.uuid, "reservation_uuid": visit.reservation_uuid})
    return [completed_out(row) for row in completed]


@app.get("/admin/visits/recent", response_model=list[CompletedVisitOut], tags=["admin-visit"])
def recent_visits(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
    offset: int = 0,
    limit: int = Query(10, enum=[5, 10, 20, 30, 50]),
) -> list[CompletedVisitOut]:
    rows = db.scalars(
        select(models.CompletedVisit)
        .where(models.CompletedVisit.no_show.is_(False))
        .order_by(models.CompletedVisit.completed_at.desc())
        .offset(max(0, offset))
        .limit(limit)
    ).all()
    return [completed_out(row) for row in rows]


@app.get("/admin/day-close/pending", response_model=list[PendingDayCloseOut], tags=["admin-day-close"])
def pending_day_closes(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> list[PendingDayCloseOut]:
    today = local_today(settings)
    schedule_dates = set(
        db.scalars(select(models.ScheduleSet.sale_date).where(models.ScheduleSet.sale_date < today)).all()
    )
    reservation_dates = set(
        db.scalars(select(models.Reservation.sale_date).where(models.Reservation.sale_date < today)).all()
    )
    closed_dates = set(db.scalars(select(models.BusinessDayClose.sale_date)).all())
    pending_dates = sorted((schedule_dates | reservation_dates) - closed_dates)
    return [pending_day_close_out(db, sale_date) for sale_date in pending_dates]


@app.post("/admin/day-close", response_model=DayCloseOut, tags=["admin-day-close"])
def close_day(
    body: DayCloseRequest,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> DayCloseOut:
    target_date = body.sale_date or local_today(settings)
    if target_date > local_today(settings):
        raise HTTPException(status_code=400, detail="future sale date cannot be closed")
    if not body.confirm_no_show:
        raise HTTPException(
            status_code=400,
            detail="마감 처리는 미방문 예약을 노쇼로 바꿉니다. 확인 후 confirm_no_show=true로 다시 요청하세요.",
        )
    already_closed = db.scalar(
        select(models.BusinessDayClose).where(models.BusinessDayClose.sale_date == target_date).with_for_update()
    )
    if already_closed:
        raise HTTPException(status_code=409, detail="business day is already closed")
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.sale_date == target_date, models.Reservation.status == "reserved")
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .with_for_update()
    ).all()
    no_show_users: dict[int, models.User] = {}
    no_show_reservations = 0
    visits: list[models.CompletedVisit] = []
    for row in rows:
        row.status = "no_show"
        no_show_reservations += 1
        no_show_users[row.user_id] = row.user
        visit = completed_row(row, no_show=True)
        db.add(visit)
        visits.append(visit)
    for user_id, user in no_show_users.items():
        count = db.scalar(select(models.NoShowCount).where(models.NoShowCount.user_id == user_id))
        if not count:
            count = models.NoShowCount(user_id=user_id, nickname=user.nickname, count=0)
            db.add(count)
        count.nickname = user.nickname
        count.count += 1
    threshold = int(setting_value(db, "noshow_ban_threshold", "2"))
    db.flush()
    auto_banned = 0
    for user_id, user in no_show_users.items():
        count = db.scalar(select(models.NoShowCount).where(models.NoShowCount.user_id == user_id))
        if count and count.count >= threshold and not is_user_banned(db, user):
            db.add(models.BannedUser(user_id=user.id, user_uuid=user.uuid, nickname=user.nickname, reason=f"노쇼 {count.count}회", active=True))
            auto_banned += 1
    visited_reservations = reservation_count(db, target_date, "visited")
    processed_reservations = len(rows) + visited_reservations
    close_record = models.BusinessDayClose(
        sale_date=target_date,
        processed_reservations=processed_reservations,
        visited_reservations=visited_reservations,
        no_show_users=len(no_show_users),
        no_show_reservations=no_show_reservations,
        auto_banned_users=auto_banned,
    )
    db.add(close_record)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="business day is already closed") from exc
    db.refresh(close_record)
    message_bus.publish(
        "day.closed",
        {
            "sale_date": target_date.isoformat(),
            "no_show_users": len(no_show_users),
            "no_show_reservations": no_show_reservations,
            "auto_banned_users": auto_banned,
        },
    )
    return DayCloseOut(
        sale_date=target_date,
        closed_at=close_record.closed_at,
        processed_reservations=processed_reservations,
        visited_reservations=visited_reservations,
        no_show_users=len(no_show_users),
        no_show_reservations=no_show_reservations,
        auto_banned_users=auto_banned,
    )


@app.get("/admin/banned-users", response_model=list[BannedUserOut], tags=["admin-users"])
def list_banned_users(_: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> list[models.BannedUser]:
    return list(db.scalars(select(models.BannedUser).order_by(models.BannedUser.created_at.desc())).all())


@app.post("/admin/banned-users", response_model=BannedUserOut, status_code=201, tags=["admin-users"])
def add_banned_user(body: BannedUserIn, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> models.BannedUser:
    user = db.scalar(select(models.User).where(models.User.uuid == body.user_uuid))
    ban = db.scalar(select(models.BannedUser).where(models.BannedUser.user_uuid == body.user_uuid))
    if not ban:
        ban = models.BannedUser(user_id=user.id if user else None, user_uuid=body.user_uuid)
        db.add(ban)
    ban.nickname = body.nickname or (user.nickname if user else "")
    ban.reason = body.reason
    ban.active = body.active
    db.commit()
    db.refresh(ban)
    return ban


@app.delete("/admin/banned-users/{user_uuid}", status_code=204, tags=["admin-users"])
def remove_banned_user(user_uuid: str, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> None:
    ban = db.scalar(select(models.BannedUser).where(models.BannedUser.user_uuid == user_uuid, models.BannedUser.active.is_(True)))
    if ban:
        ban.active = False
        db.commit()


@app.get("/admin/ip-bans", response_model=list[IpBanOut], tags=["admin-ip-bans"])
def list_ip_bans(_: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> list[models.IpBan]:
    return list(db.scalars(select(models.IpBan).order_by(models.IpBan.created_at.desc())).all())


@app.post("/admin/ip-bans", response_model=IpBanOut, status_code=201, tags=["admin-ip-bans"])
def add_ip_ban(body: IpBanIn, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> models.IpBan:
    ban = db.scalar(select(models.IpBan).where(models.IpBan.ip_address == body.ip_address, models.IpBan.actor_key == body.actor_key))
    if not ban:
        ban = models.IpBan(ip_address=body.ip_address, actor_key=body.actor_key)
        db.add(ban)
    ban.actor_key = body.actor_key
    ban.user_uuid = body.user_uuid
    ban.nickname = body.nickname
    ban.reason = body.reason
    ban.active = body.active
    db.commit()
    db.refresh(ban)
    return ban


@app.delete("/admin/ip-bans/{ip_address}", status_code=204, tags=["admin-ip-bans"])
def delete_ip_ban(
    ip_address: str,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
    actor_key: str | None = None,
) -> None:
    stmt = delete(models.IpBan).where(models.IpBan.ip_address == ip_address)
    if actor_key is not None:
        stmt = stmt.where(models.IpBan.actor_key == actor_key)
    db.execute(stmt)
    db.commit()


@app.get("/admin/settings/{key}", response_model=SettingOut, tags=["admin-settings"])
def get_setting(key: str, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> SettingOut:
    return SettingOut(key=key, value=setting_value(db, key, ""))


@app.put("/admin/settings/{key}", response_model=SettingOut, tags=["admin-settings"])
def put_setting(
    key: str,
    body: SettingUpdate,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> SettingOut:
    setting = db.get(models.AppSetting, key)
    if not setting:
        setting = models.AppSetting(key=key, value=body.value)
        db.add(setting)
    else:
        setting.value = body.value
    db.commit()
    return SettingOut(key=key, value=body.value)


@app.get("/settings/{key}", response_model=SettingOut, tags=["settings"])
def public_setting(key: str, db: Session = Depends(get_db)) -> SettingOut:
    public_keys = {"disclaimer_markdown", "reservation_agreement_markdown", BLOCKED_USER_MESSAGE_KEY}
    if key not in public_keys:
        raise HTTPException(status_code=404, detail="public setting not found")
    return SettingOut(key=key, value=setting_value(db, key, ""))


def require_schedule_set(db: Session, schedule_set_uuid: str) -> models.ScheduleSet:
    schedule_set = db.scalar(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.uuid == schedule_set_uuid)
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
    )
    if not schedule_set:
        raise HTTPException(status_code=404, detail="판매 일정을 찾을 수 없습니다.")
    return schedule_set


def schedule_out(db: Session, row: models.ScheduleSet, include_remaining: bool = False) -> ScheduleSetOut:
    return ScheduleSetOut(
        uuid=row.uuid,
        sale_date=row.sale_date,
        title=row.title,
        can_bundle=row.can_bundle,
        has_time_slots=row.has_time_slots,
        reservation_open=row.reservation_open,
        products=[
            {
                "uuid": product.uuid,
                "group_uuid": product.group.uuid,
                "group_name": product.group.name,
                "group_purchase_limit": product.group.purchase_limit,
                "name": product.name,
                "stock_quantity": product.stock_quantity,
                "stock_remaining": stock_remaining(db, product) if include_remaining else None,
                "per_user_limit": product.per_user_limit,
            }
            for product in row.products
        ],
        time_slots=[
            {
                "uuid": slot.uuid,
                "code": slot.code,
                "label": slot.label,
                "capacity": slot.capacity,
                "remaining_customers": time_slot_remaining(db, slot) if include_remaining else None,
            }
            for slot in sorted(row.time_slots, key=lambda item: item.code)
        ],
    )


def reservation_out(row: models.Reservation) -> ReservationOut:
    return ReservationOut(
        uuid=row.uuid,
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
            db.scalar(
                select(func.count(models.ScheduleSet.id)).where(models.ScheduleSet.sale_date == sale_date)
            )
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


def should_skip_rate_limit(path: str) -> bool:
    return path in RATE_LIMIT_SKIP_PATHS or any(path.startswith(prefix) for prefix in RATE_LIMIT_SKIP_PREFIXES)


def active_ip_ban(db: Session, ip_address: str, actor_key: str = "") -> models.IpBan | None:
    actor_candidates = [""] if not actor_key else ["", actor_key]
    return db.scalar(
        select(models.IpBan)
        .where(
            models.IpBan.ip_address == ip_address,
            models.IpBan.actor_key.in_(actor_candidates),
            models.IpBan.active.is_(True),
        )
        .order_by(models.IpBan.actor_key.desc())
    )


def ip_ban_response(request: Request, db: Session, ban: models.IpBan) -> JSONResponse:
    message = setting_value(db, BLOCKED_USER_MESSAGE_KEY, DEFAULT_BLOCKED_USER_MESSAGE)
    payload = {
        "detail": {
            "code": "ip_banned",
            "message": message,
            "ip_address": ban.ip_address,
            "actor_key": ban.actor_key,
            "user_uuid": ban.user_uuid,
            "nickname": ban.nickname,
            "reason": ban.reason,
        }
    }
    response = JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=payload)
    origin = request.headers.get("origin")
    if origin and origin in settings.cors_allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response


def rate_limit_actor(db: Session, request: Request) -> tuple[str, str, str]:
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        return "", "", ""
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = verify_access_token(token, settings)
    except HTTPException:
        return "", "", ""
    if payload.get("scope") == "admin":
        return "admin", "", "관리자"
    if payload.get("scope") != "user":
        return "", "", ""
    user = db.scalar(select(models.User).where(models.User.uuid == payload.get("sub")))
    if not user:
        return "", "", ""
    return f"user:{user.uuid}", user.uuid, user.nickname


def rate_limit_route_key(request: Request) -> str:
    normalized_path = UUID_PATH_PART.sub("/{uuid}", request.url.path)
    return f"{request.method.upper()} {normalized_path}"


def rate_limit_window_start(now: datetime) -> datetime:
    return now.replace(microsecond=0)


def apply_anti_macro_limit(db: Session, request: Request, ip_address: str) -> models.IpBan | None:
    now = models.now_utc()
    actor_key, user_uuid, nickname = rate_limit_actor(db, request)
    if not actor_key:
        return None

    route_key = rate_limit_route_key(request)
    window_started_at = rate_limit_window_start(now)
    cleanup_before = now - timedelta(seconds=settings.anti_macro_retention_seconds)
    db.execute(delete(models.IpRateLimit).where(models.IpRateLimit.window_started_at < cleanup_before))

    row = db.scalar(
        select(models.IpRateLimit)
        .where(
            models.IpRateLimit.ip_address == ip_address,
            models.IpRateLimit.actor_key == actor_key,
            models.IpRateLimit.route_key == route_key,
            models.IpRateLimit.window_started_at == window_started_at,
        )
        .with_for_update()
    )
    if not row:
        row = models.IpRateLimit(
            ip_address=ip_address,
            actor_key=actor_key,
            user_uuid=user_uuid,
            nickname=nickname,
            route_key=route_key,
            window_started_at=window_started_at,
            request_count=0,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            row = db.scalar(
                select(models.IpRateLimit)
                .where(
                    models.IpRateLimit.ip_address == ip_address,
                    models.IpRateLimit.actor_key == actor_key,
                    models.IpRateLimit.route_key == route_key,
                    models.IpRateLimit.window_started_at == window_started_at,
                )
                .with_for_update()
            )
            if not row:
                raise

    row.request_count += 1
    row.updated_at = now
    row.user_uuid = user_uuid or row.user_uuid
    row.nickname = nickname or row.nickname
    if row.request_count < settings.anti_macro_duration:
        db.commit()
        return None

    reason = f"초당 동일 API 요청 {row.request_count}회 감지(차단 기준 {settings.anti_macro_duration}회, API {route_key})"
    ban = db.scalar(
        select(models.IpBan)
        .where(models.IpBan.ip_address == ip_address, models.IpBan.actor_key == actor_key)
        .with_for_update()
    )
    if not ban:
        ban = models.IpBan(ip_address=ip_address, actor_key=actor_key)
        db.add(ban)
    ban.active = True
    ban.user_uuid = row.user_uuid
    ban.nickname = row.nickname
    ban.reason = reason
    db.commit()
    db.refresh(ban)
    return ban
