from __future__ import annotations

import json
import hmac
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import models
from .config import load_settings
from .database import Base, engine, get_db
from .message_bus import get_message_bus
from .manual_code_registry import ManualCodeCollisionError, get_manual_code_registry
from .otp import qr_hash, remaining_seconds, verify_token
from .read_models import (
    completed_out,
    completed_row,
    is_user_banned,
    pending_day_close_out,
    reservation_count,
    reservation_out,
    reservation_request_out,
    schedule_out,
    setting_value,
)
from .reservation_queue import ReservationQueueError, enqueue_reservation_request
from .reservation_service import mutation_payload_json, process_reservation_request, reservation_payload_json
from .schemas import (
    AdminLoginRequest,
    AdminScanRequest,
    BannedUserIn,
    BannedUserOut,
    CompleteVisitRequest,
    CompletedVisitOut,
    DayCloseOut,
    DayCloseRequest,
    KakaoCodeExchangeRequest,
    ManualVisitCodeRequest,
    NicknameRequest,
    PendingDayCloseOut,
    ReservationCreate,
    ReservationOut,
    ReservationRequestCreate,
    ReservationRequestOut,
    ReservationUpdateRequestCreate,
    ReservationWorkerRequest,
    ScheduleDateOperationOut,
    ScheduleSetCreate,
    ScheduleSetOut,
    ScheduleSetPageOut,
    ScheduleSetPatch,
    SettingOut,
    SettingUpdate,
    TokenResponse,
    UserOut,
    UserTokenResponse,
    VisitManualCodeOut,
    VisitScanOut,
    VisitTokenListOut,
    VisitTokenOut,
    WaitingRoomSyncOut,
)
from .security import create_access_token, require_admin, require_user
from .timezone import local_today


settings = load_settings()
message_bus = get_message_bus(settings)
manual_code_registry = get_manual_code_registry(settings)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

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

@app.on_event("startup")
def create_local_dev_schema() -> None:
    if settings.is_dev_mode and settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "environment": settings.environment}


@app.get("/static/personal.md", tags=["static"])
def personal_policy_markdown() -> Response:
    path = PROJECT_ROOT / "static" / "personal.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="personal policy not found")
    return Response(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


def require_reservation_gateway(
    x_reservation_gateway_secret: str | None = Header(None, alias="X-Reservation-Gateway-Secret"),
) -> None:
    if settings.reservation_gateway_secret and not hmac.compare_digest(
        x_reservation_gateway_secret or "",
        settings.reservation_gateway_secret,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reservation gateway required")


def require_reservation_worker(
    x_reservation_worker_secret: str | None = Header(None, alias="X-Reservation-Worker-Secret"),
) -> None:
    if settings.reservation_worker_secret and not hmac.compare_digest(
        x_reservation_worker_secret or "",
        settings.reservation_worker_secret,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="reservation worker required")


IN_FLIGHT_REQUEST_STATUSES = ("pending", "enqueued", "processing")


def submit_reservation_request(
    request: Request,
    db: Session,
    request_row: models.ReservationRequest,
) -> ReservationRequestOut:
    db.add(request_row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(models.ReservationRequest).where(
                models.ReservationRequest.user_id == request_row.user_id,
                models.ReservationRequest.idempotency_key == request_row.idempotency_key,
            )
        )
        if existing:
            return require_matching_existing_request(
                db,
                existing,
                request_row.action,
                request_row.target_reservation_uuid,
            )
        raise
    db.refresh(request_row)

    if settings.reservation_queue_backend == "inline":
        processed = process_reservation_request(db, request_row.uuid)
        return reservation_request_out(db, processed)
    if settings.reservation_queue_backend == "none":
        return reservation_request_out(db, request_row)

    worker_url = settings.reservation_worker_url or str(request.url_for("process_reservation_worker"))
    try:
        task_name = enqueue_reservation_request(request_row.uuid, settings, worker_url)
    except ReservationQueueError as exc:
        request_row = db.scalar(select(models.ReservationRequest).where(models.ReservationRequest.id == request_row.id))
        if request_row:
            request_row.status = "enqueue_failed"
            request_row.error_message = str(exc)
            request_row.updated_at = models.now_utc()
            db.commit()
            return reservation_request_out(db, request_row)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    db.expire(request_row)
    request_row = db.scalar(
        select(models.ReservationRequest)
        .where(models.ReservationRequest.id == request_row.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="reservation request not found")
    if request_row.status == "pending":
        request_row.status = "enqueued"
        request_row.task_name = task_name
        request_row.updated_at = models.now_utc()
        db.commit()
    return reservation_request_out(db, request_row)


def find_existing_reservation_request(db: Session, user_id: int, idempotency_key: str) -> models.ReservationRequest | None:
    return db.scalar(
        select(models.ReservationRequest).where(
            models.ReservationRequest.user_id == user_id,
            models.ReservationRequest.idempotency_key == idempotency_key,
        )
    )


def require_matching_existing_request(
    db: Session,
    existing: models.ReservationRequest,
    action: str,
    target_reservation_uuid: str = "",
) -> ReservationRequestOut:
    if existing.action != action or existing.target_reservation_uuid != target_reservation_uuid:
        raise HTTPException(status_code=409, detail="같은 idempotency key가 다른 예약 변경 요청에 이미 사용되었습니다.")
    return reservation_request_out(db, existing)


def require_mutable_reservation_for_request(
    db: Session,
    user_id: int,
    reservation_uuid: str,
) -> models.Reservation:
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
        raise HTTPException(status_code=404, detail="변경 가능한 예약을 찾을 수 없습니다.")
    existing_in_flight = db.scalar(
        select(models.ReservationRequest)
        .where(
            models.ReservationRequest.user_id == user_id,
            models.ReservationRequest.target_reservation_uuid == reservation_uuid,
            models.ReservationRequest.action.in_(["update", "cancel"]),
            models.ReservationRequest.status.in_(IN_FLIGHT_REQUEST_STATUSES),
        )
        .with_for_update()
    )
    if existing_in_flight:
        raise HTTPException(status_code=409, detail="이미 처리 대기 중인 예약 변경 요청이 있습니다.")
    return reservation


def waiting_room_sync_payload(sale_date: date, desired_open: bool, event: str, schedules_count: int) -> WaitingRoomSyncOut:
    redis_key = f"wr:open:{sale_date.isoformat()}"
    effective_open = desired_open and schedules_count > 0
    if desired_open and schedules_count == 0:
        message = "해당 날짜에 등록된 판매 일정이 없어 Vercel waiting room을 열지 않습니다."
    elif effective_open:
        message = "Vercel Redis에서 이 날짜 waiting room을 열어 주세요."
    else:
        message = "Vercel Redis에서 이 날짜 waiting room을 닫아 주세요."
    return WaitingRoomSyncOut(
        sale_date=sale_date,
        redis_key=redis_key,
        desired_open=effective_open,
        redis_value="1" if effective_open else "0",
        delete_key=not effective_open,
        event=event,
        message=message,
    )


def delete_schedule_dependents(db: Session, schedule_set: models.ScheduleSet) -> None:
    reservations = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.schedule_set_id == schedule_set.id)
        .with_for_update()
    ).all()
    reservation_ids = [row.id for row in reservations]
    reservation_uuids = [row.uuid for row in reservations]

    if reservation_ids or reservation_uuids:
        visit_filters = []
        if reservation_ids:
            visit_filters.append(models.CompletedVisit.reservation_id.in_(reservation_ids))
        if reservation_uuids:
            visit_filters.append(models.CompletedVisit.reservation_uuid.in_(reservation_uuids))
        db.execute(delete(models.CompletedVisit).where(or_(*visit_filters)))

    request_filters = [models.ReservationRequest.payload_json.like(f"%{schedule_set.uuid}%")]
    if reservation_uuids:
        request_filters.append(models.ReservationRequest.target_reservation_uuid.in_(reservation_uuids))
        for reservation_uuid in reservation_uuids:
            request_filters.append(models.ReservationRequest.result_json.like(f"%{reservation_uuid}%"))
    db.execute(delete(models.ReservationRequest).where(or_(*request_filters)))

    db.execute(delete(models.Reservation).where(models.Reservation.schedule_set_id == schedule_set.id))


@app.post("/admin/login", response_model=TokenResponse, tags=["admin-auth"])
def admin_login(body: AdminLoginRequest) -> TokenResponse:
    ok = body.admin_id == settings.admin_id and body.password == settings.admin_password
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin credentials")

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


@app.patch("/users/me/nickname", response_model=UserOut, tags=["users"])
def update_nickname(body: NicknameRequest, user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> models.User:
    user.nickname = body.nickname.strip()
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/users/me", status_code=204, tags=["users"])
def delete_me(user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> None:
    try:
        manual_code_registry.delete_user(user.uuid)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="manual visit code cleanup failed") from exc

    db.scalars(
        select(models.ReservationRequest).where(models.ReservationRequest.user_id == user.id).with_for_update()
    ).all()

    locked_user = db.scalar(select(models.User).where(models.User.id == user.id).with_for_update())
    if not locked_user:
        raise HTTPException(status_code=404, detail="user not found")

    reservations = db.scalars(
        select(models.Reservation).where(models.Reservation.user_id == locked_user.id).with_for_update()
    ).all()
    reservation_uuids = [row.uuid for row in reservations]

    completed_visit_filters = [
        models.CompletedVisit.user_id == locked_user.id,
        models.CompletedVisit.user_uuid == locked_user.uuid,
    ]
    if reservation_uuids:
        completed_visit_filters.append(models.CompletedVisit.reservation_uuid.in_(reservation_uuids))

    db.execute(delete(models.CompletedVisit).where(or_(*completed_visit_filters)))
    db.execute(delete(models.NoShowCount).where(models.NoShowCount.user_id == locked_user.id))
    db.execute(
        delete(models.BannedUser).where(
            or_(
                models.BannedUser.user_id == locked_user.id,
                models.BannedUser.user_uuid == locked_user.uuid,
            )
        )
    )
    db.execute(
        delete(models.ReservationRequest).where(
            or_(
                models.ReservationRequest.user_id == locked_user.id,
                models.ReservationRequest.user_uuid == locked_user.uuid,
            )
        )
    )
    db.execute(delete(models.Reservation).where(models.Reservation.user_id == locked_user.id))
    db.delete(locked_user)
    db.commit()

    try:
        manual_code_registry.delete_user(user.uuid)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="manual visit code cleanup failed") from exc


@app.get("/schedules/open", response_model=list[ScheduleSetOut], tags=["schedules"])
def public_open_schedules(db: Session = Depends(get_db)) -> list[ScheduleSetOut]:
    rows = db.scalars(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.reservation_open.is_(True), models.ScheduleSet.sale_date >= local_today(settings))
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.sale_date, models.ScheduleSet.title)
    ).all()
    return [schedule_out(db, row, include_remaining=True, respect_visibility=True) for row in rows]


@app.get("/admin/schedules", response_model=list[ScheduleSetOut], tags=["admin-schedules"])
def admin_list_schedules(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> list[ScheduleSetOut]:
    rows = db.scalars(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.sale_date >= local_today(settings))
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.sale_date, models.ScheduleSet.title)
    ).all()
    return [schedule_out(db, row, include_remaining=True, respect_visibility=False) for row in rows]


@app.get("/admin/schedules/past", response_model=ScheduleSetPageOut, tags=["admin-schedules"])
def admin_list_past_schedules(
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> ScheduleSetPageOut:
    today = local_today(settings)
    total = int(db.scalar(select(func.count(models.ScheduleSet.id)).where(models.ScheduleSet.sale_date < today)) or 0)
    rows = db.scalars(
        select(models.ScheduleSet)
        .where(models.ScheduleSet.sale_date < today)
        .options(selectinload(models.ScheduleSet.products).selectinload(models.SaleProduct.group), selectinload(models.ScheduleSet.time_slots))
        .order_by(models.ScheduleSet.sale_date.desc(), models.ScheduleSet.title)
        .offset(offset)
        .limit(limit)
    ).all()
    return ScheduleSetPageOut(
        items=[schedule_out(db, row, include_remaining=True, respect_visibility=False) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
        has_more=offset + len(rows) < total,
    )


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
        show_stock_remaining=body.show_stock_remaining,
        show_time_slot_remaining=body.show_time_slot_remaining,
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
    return schedule_out(db, schedule_set, include_remaining=True, respect_visibility=False)


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
    if body.show_stock_remaining is not None:
        schedule_set.show_stock_remaining = body.show_stock_remaining
    if body.show_time_slot_remaining is not None:
        schedule_set.show_time_slot_remaining = body.show_time_slot_remaining
    db.commit()
    db.refresh(schedule_set)
    return schedule_out(db, schedule_set, include_remaining=True, respect_visibility=False)


@app.delete("/admin/schedules/{schedule_set_uuid}", status_code=204, tags=["admin-schedules"])
def delete_schedule_set(schedule_set_uuid: str, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> None:
    schedule_set = require_schedule_set(db, schedule_set_uuid)
    delete_schedule_dependents(db, schedule_set)
    db.delete(schedule_set)
    db.commit()


@app.post("/admin/schedule-dates/{sale_date}/open", response_model=ScheduleDateOperationOut, tags=["admin-schedules"])
def open_schedule_date(sale_date: date, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> ScheduleDateOperationOut:
    rows = db.scalars(select(models.ScheduleSet).where(models.ScheduleSet.sale_date == sale_date)).all()
    for row in rows:
        row.reservation_open = True
    db.commit()
    schedules = [schedule_out(db, row, include_remaining=True, respect_visibility=False) for row in rows]
    return ScheduleDateOperationOut(
        sale_date=sale_date,
        schedules=schedules,
        waiting_room_sync=waiting_room_sync_payload(
            sale_date,
            desired_open=True,
            event="schedule_date_opened",
            schedules_count=len(rows),
        ),
    )


@app.post("/admin/schedule-dates/{sale_date}/close", response_model=ScheduleDateOperationOut, tags=["admin-schedules"])
def close_schedule_date(sale_date: date, _: Annotated[dict, Depends(require_admin)], db: Session = Depends(get_db)) -> ScheduleDateOperationOut:
    rows = db.scalars(select(models.ScheduleSet).where(models.ScheduleSet.sale_date == sale_date)).all()
    for row in rows:
        row.reservation_open = False
    db.commit()
    schedules = [schedule_out(db, row, include_remaining=True, respect_visibility=False) for row in rows]
    return ScheduleDateOperationOut(
        sale_date=sale_date,
        schedules=schedules,
        waiting_room_sync=waiting_room_sync_payload(
            sale_date,
            desired_open=False,
            event="schedule_date_closed",
            schedules_count=len(rows),
        ),
    )


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


@app.post("/reservations", include_in_schema=False, tags=["reservations"])
def create_reservation(
    body: ReservationCreate,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    raise HTTPException(status_code=410, detail="예약 생성은 POST /reservation-requests를 사용해 주세요.")


@app.post("/reservation-requests", response_model=ReservationRequestOut, status_code=202, tags=["reservation-requests"])
def create_reservation_request(
    request: Request,
    body: ReservationRequestCreate,
    _: Annotated[None, Depends(require_reservation_gateway)],
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReservationRequestOut:
    if not body.items:
        raise HTTPException(status_code=400, detail="예약할 상품을 선택해 주세요.")
    idempotency_key = (body.idempotency_key or "").strip() or models.new_uuid()
    existing = find_existing_reservation_request(db, user.id, idempotency_key)
    if existing:
        return require_matching_existing_request(db, existing, "create", "")

    payload_body = ReservationCreate(sale_date=body.sale_date, items=body.items)
    request_row = models.ReservationRequest(
        user_id=user.id,
        user_uuid=user.uuid,
        sale_date=body.sale_date,
        action="create",
        target_reservation_uuid="",
        idempotency_key=idempotency_key,
        payload_json=reservation_payload_json(payload_body),
        status="pending",
    )
    return submit_reservation_request(request, db, request_row)


@app.get("/reservation-requests/{request_uuid}", response_model=ReservationRequestOut, tags=["reservation-requests"])
def get_reservation_request(
    request_uuid: str,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReservationRequestOut:
    request_row = db.scalar(
        select(models.ReservationRequest).where(
            models.ReservationRequest.uuid == request_uuid,
            models.ReservationRequest.user_id == user.id,
        )
    )
    if not request_row:
        raise HTTPException(status_code=404, detail="reservation request not found")
    return reservation_request_out(db, request_row)


@app.post("/internal/reservation-worker", response_model=ReservationRequestOut, tags=["internal"])
def process_reservation_worker(
    body: ReservationWorkerRequest,
    _: Annotated[None, Depends(require_reservation_worker)],
    db: Session = Depends(get_db),
) -> ReservationRequestOut:
    request_row = process_reservation_request(db, body.request_uuid)
    return reservation_request_out(db, request_row)


@app.get("/users/me/reservations", response_model=list[ReservationOut], tags=["reservations"])
def my_reservations(user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> list[ReservationOut]:
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.user_id == user.id, models.Reservation.status == "reserved")
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .order_by(models.Reservation.sale_date, models.Reservation.reserved_at)
    ).all()
    return [reservation_out(row) for row in rows]


@app.patch("/reservations/{reservation_uuid}", response_model=ReservationRequestOut, status_code=202, tags=["reservations"])
def update_reservation(
    reservation_uuid: str,
    request: Request,
    body: ReservationUpdateRequestCreate,
    _: Annotated[None, Depends(require_reservation_gateway)],
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReservationRequestOut:
    idempotency_key = (body.idempotency_key or "").strip() or models.new_uuid()
    existing = find_existing_reservation_request(db, user.id, idempotency_key)
    if existing:
        return require_matching_existing_request(db, existing, "update", reservation_uuid)
    reservation = require_mutable_reservation_for_request(db, user.id, reservation_uuid)
    request_row = models.ReservationRequest(
        user_id=user.id,
        user_uuid=user.uuid,
        sale_date=reservation.sale_date,
        action="update",
        target_reservation_uuid=reservation.uuid,
        idempotency_key=idempotency_key,
        payload_json=mutation_payload_json(body),
        status="pending",
    )
    return submit_reservation_request(request, db, request_row)


@app.delete("/reservations/{reservation_uuid}", response_model=ReservationRequestOut, status_code=202, tags=["reservations"])
def cancel_reservation(
    reservation_uuid: str,
    request: Request,
    _: Annotated[None, Depends(require_reservation_gateway)],
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
    idempotency_key_header: str | None = Header(None, alias="Idempotency-Key"),
) -> ReservationRequestOut:
    idempotency_key = (idempotency_key_header or "").strip() or models.new_uuid()
    existing = find_existing_reservation_request(db, user.id, idempotency_key)
    if existing:
        return require_matching_existing_request(db, existing, "cancel", reservation_uuid)
    reservation = require_mutable_reservation_for_request(db, user.id, reservation_uuid)
    request_row = models.ReservationRequest(
        user_id=user.id,
        user_uuid=user.uuid,
        sale_date=reservation.sale_date,
        action="cancel",
        target_reservation_uuid=reservation.uuid,
        idempotency_key=idempotency_key,
        payload_json="{}",
        status="pending",
    )
    return submit_reservation_request(request, db, request_row)


@app.get("/users/me/visit-tokens", response_model=VisitTokenListOut, tags=["visit"])
def my_visit_tokens(user: models.User = Depends(require_user), db: Session = Depends(get_db)) -> VisitTokenListOut:
    today = local_today(settings)
    rows = db.scalars(
        select(models.Reservation)
        .where(
            models.Reservation.user_id == user.id,
            models.Reservation.sale_date == today,
            models.Reservation.status == "reserved",
        )
        .options(selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
        .order_by(models.Reservation.sale_date, models.Reservation.reserved_at)
    ).all()
    if not rows:
        future_exists = bool(
            db.scalar(
                select(models.Reservation.id).where(
                    models.Reservation.user_id == user.id,
                    models.Reservation.sale_date > today,
                    models.Reservation.status == "reserved",
                )
            )
        )
        message = "방문 확인 코드는 예약 당일에만 발급됩니다." if future_exists else "오늘 발급 가능한 방문 확인 코드가 없습니다."
        return VisitTokenListOut(tokens=[], message=message)
    window = f"t-{settings.otp_delta},t,t+{settings.otp_delta}" if settings.otp_delta else "t"
    tokens = []
    for row in rows:
        tokens.append(
            VisitTokenOut(
                reservation_uuid=row.uuid,
                sale_date=row.sale_date,
                schedule_set_uuid=row.schedule_set.uuid,
                schedule_title=row.schedule_set.title,
                product_name=row.product.name,
                quantity=row.quantity,
                time_slot_label=row.time_slot.label if row.time_slot else "",
                qr_hash=qr_hash(user.uuid, row.uuid, settings),
                expires_in_seconds=remaining_seconds(settings),
                hash_window=window,
            )
        )
    return VisitTokenListOut(tokens=tokens)


@app.post("/users/me/visit-manual-code", response_model=VisitManualCodeOut, tags=["visit"])
def issue_my_manual_visit_code(
    body: ManualVisitCodeRequest,
    user: models.User = Depends(require_user),
    db: Session = Depends(get_db),
) -> VisitManualCodeOut:
    today = local_today(settings)
    reservation = db.scalar(
        select(models.Reservation)
        .where(
            models.Reservation.uuid == body.reservation_uuid,
            models.Reservation.user_id == user.id,
        )
        .options(selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
    )
    if not reservation:
        raise HTTPException(status_code=404, detail="예약을 찾을 수 없습니다.")
    if reservation.status != "reserved":
        raise HTTPException(status_code=400, detail="방문 확인 코드 발급이 가능한 예약이 아닙니다.")
    if reservation.sale_date > today:
        raise HTTPException(status_code=400, detail="방문 확인 수동 코드는 예약 당일에만 발급됩니다.")
    if reservation.sale_date < today:
        raise HTTPException(status_code=400, detail="지난 예약은 방문 확인 수동 코드를 발급할 수 없습니다.")

    try:
        reserved_manual_code = manual_code_registry.reserve(user.uuid, reservation.uuid, settings)
    except ManualCodeCollisionError as exc:
        raise HTTPException(status_code=503, detail="수동 코드 충돌을 해결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="수동 코드 저장소를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.") from exc

    window = f"t-{settings.otp_delta},t,t+{settings.otp_delta}" if settings.otp_delta else "t"
    return VisitManualCodeOut(
        reservation_uuid=reservation.uuid,
        sale_date=reservation.sale_date,
        schedule_set_uuid=reservation.schedule_set.uuid,
        schedule_title=reservation.schedule_set.title,
        product_name=reservation.product.name,
        quantity=reservation.quantity,
        time_slot_label=reservation.time_slot.label if reservation.time_slot else "",
        manual_code=reserved_manual_code.code,
        expires_in_seconds=remaining_seconds(settings),
        hash_window=window,
    )


@app.post("/admin/visits/scan", response_model=VisitScanOut, tags=["admin-visit"])
def scan_visit_token(
    body: AdminScanRequest,
    _: Annotated[dict, Depends(require_admin)],
    db: Session = Depends(get_db),
) -> VisitScanOut:
    normalized_token = body.token.strip().lower()
    if body.token_type in {"auto", "manual"}:
        try:
            resolved = manual_code_registry.resolve(normalized_token)
        except Exception as exc:
            if body.token_type == "manual":
                raise HTTPException(status_code=503, detail="수동 코드 저장소를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.") from exc
            resolved = None
        if resolved:
            reservation = db.scalar(
                select(models.Reservation)
                .where(
                    models.Reservation.uuid == resolved.reservation_uuid,
                    models.Reservation.status == "reserved",
                    models.Reservation.sale_date == local_today(settings),
                )
                .options(
                    selectinload(models.Reservation.user),
                    selectinload(models.Reservation.product),
                    selectinload(models.Reservation.schedule_set),
                    selectinload(models.Reservation.time_slot),
                )
            )
            if reservation and reservation.user.uuid == resolved.user_uuid:
                return VisitScanOut(
                    user_uuid=reservation.user.uuid,
                    nickname=reservation.user.nickname,
                    reservation=reservation_out(reservation),
                )
        if body.token_type == "manual":
            raise HTTPException(status_code=400, detail="수동 코드가 만료되었거나 올바르지 않습니다.")
    rows = db.scalars(
        select(models.Reservation)
        .where(models.Reservation.status == "reserved", models.Reservation.sale_date == local_today(settings))
        .options(selectinload(models.Reservation.user), selectinload(models.Reservation.product), selectinload(models.Reservation.schedule_set), selectinload(models.Reservation.time_slot))
    ).all()
    fallback_token_type = "qr" if body.token_type == "auto" else body.token_type
    for reservation in rows:
        if verify_token(body.token, reservation.user.uuid, reservation.uuid, settings, fallback_token_type):
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
    public_keys = {"disclaimer_markdown", "reservation_agreement_markdown"}
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
