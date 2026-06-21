from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    detail: str = Field(..., examples=["예약 가능한 재고가 부족합니다."])


class AdminLoginRequest(BaseModel):
    admin_id: str = Field(..., examples=["admin"])
    password: str = Field(..., examples=["change-me"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class KakaoCodeExchangeRequest(BaseModel):
    code: str = Field(..., examples=["kakao-oauth-authorization-code"])
    redirect_uri: str = Field(..., examples=["https://frontend.example.com/oauth/kakao/callback"])


class UserTokenResponse(TokenResponse):
    user_uuid: str
    kakao_id: str
    nickname: str


class NicknameRequest(BaseModel):
    nickname: str = Field(..., min_length=2, max_length=24, examples=["피카츄트레이너"])


class UserOut(BaseModel):
    id: int
    uuid: str
    kakao_id: str
    nickname: str
    created_at: datetime
    last_login_at: datetime

    model_config = {"from_attributes": True}


class TimeSlotIn(BaseModel):
    label: str = Field(..., examples=["14:00"])
    capacity: int | None = Field(None, ge=0, examples=[10])


class ProductIn(BaseModel):
    name: str = Field(..., examples=["메가브레이브 1박스"])
    stock_quantity: int | None = Field(None, ge=0, examples=[48])
    per_user_limit: int = Field(1, ge=1, examples=[1])


class ProductGroupIn(BaseModel):
    name: str = Field(..., examples=["박스 상품"])
    purchase_limit: int = Field(1, ge=1, examples=[1])
    products: list[ProductIn]


class ScheduleSetCreate(BaseModel):
    sale_date: date = Field(..., examples=["2026-07-01"])
    title: str = Field(..., examples=["1차 예약"])
    can_bundle: bool = Field(True, description="같은 판매 일자 안의 다른 판매 일정과 함께 예약 가능한지 여부")
    has_time_slots: bool = True
    reservation_open: bool = False
    time_slots: list[TimeSlotIn] = Field(default_factory=list)
    groups: list[ProductGroupIn]


class ScheduleSetPatch(BaseModel):
    title: str | None = None
    can_bundle: bool | None = None
    reservation_open: bool | None = None


class TimeSlotOut(BaseModel):
    uuid: str
    code: int
    label: str
    capacity: int | None
    remaining_customers: int | None = None


class ProductOut(BaseModel):
    uuid: str
    group_uuid: str
    group_name: str
    group_purchase_limit: int
    name: str
    stock_quantity: int | None
    stock_remaining: int | None = None
    per_user_limit: int


class ScheduleSetOut(BaseModel):
    uuid: str
    sale_date: date
    title: str
    can_bundle: bool
    has_time_slots: bool
    reservation_open: bool
    products: list[ProductOut]
    time_slots: list[TimeSlotOut]


class WaitingRoomSyncOut(BaseModel):
    sale_date: date
    redis_key: str
    desired_open: bool
    redis_value: str
    delete_key: bool = False
    event: Literal["schedule_date_opened", "schedule_date_closed"]
    message: str = ""


class ScheduleDateOperationOut(BaseModel):
    sale_date: date
    schedules: list[ScheduleSetOut]
    waiting_room_sync: WaitingRoomSyncOut


class ReservationItemIn(BaseModel):
    schedule_set_uuid: str
    product_uuid: str
    quantity: int = Field(1, ge=1)
    time_slot_uuid: str | None = None


class ReservationCreate(BaseModel):
    sale_date: date
    items: list[ReservationItemIn]


class ReservationRequestCreate(ReservationCreate):
    action: Literal["create"] = "create"
    idempotency_key: str | None = Field(
        None,
        min_length=8,
        max_length=96,
        examples=["client-generated-reservation-attempt-uuid"],
    )


class ReservationWorkerRequest(BaseModel):
    request_uuid: str


class ReservationPatch(BaseModel):
    quantity: int | None = Field(None, ge=1)
    time_slot_uuid: str | None = None


class ReservationUpdateRequestCreate(ReservationPatch):
    idempotency_key: str | None = Field(
        None,
        min_length=8,
        max_length=96,
        examples=["client-generated-update-attempt-uuid"],
    )


class ReservationOut(BaseModel):
    uuid: str
    sale_date: date
    schedule_set_uuid: str
    schedule_title: str
    product_uuid: str
    product_name: str
    quantity: int
    time_slot_uuid: str | None
    time_slot_label: str
    status: str
    reserved_at: datetime


class ReservationRequestOut(BaseModel):
    request_uuid: str
    action: Literal["create", "update", "cancel"]
    status: Literal["pending", "enqueued", "processing", "success", "failed", "enqueue_failed"]
    sale_date: date
    target_reservation_uuid: str = ""
    idempotency_key: str
    error_message: str = ""
    reservations: list[ReservationOut] = Field(default_factory=list)
    attempts: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class VisitTokenOut(BaseModel):
    reservation_uuid: str
    sale_date: date
    schedule_set_uuid: str
    schedule_title: str
    product_name: str
    quantity: int
    time_slot_label: str
    qr_hash: str = Field(..., description="기본값은 HMAC-MD5 hex")
    expires_in_seconds: int
    hash_window: str = Field(..., examples=["t-1,t,t+1"])


class VisitTokenListOut(BaseModel):
    tokens: list[VisitTokenOut]
    message: str = ""


class ManualVisitCodeRequest(BaseModel):
    reservation_uuid: str


class VisitManualCodeOut(BaseModel):
    reservation_uuid: str
    sale_date: date
    schedule_set_uuid: str
    schedule_title: str
    product_name: str
    quantity: int
    time_slot_label: str
    manual_code: str = Field(..., description="기본값은 HMAC-SHA1 앞 12자리")
    expires_in_seconds: int
    hash_window: str = Field(..., examples=["t-1,t,t+1"])


class AdminScanRequest(BaseModel):
    token: str = Field(..., examples=["b6fc4c620b67d95f953a5c1c1230aaab"])
    token_type: Literal["auto", "qr", "manual"] = "qr"


class VisitScanOut(BaseModel):
    user_uuid: str
    nickname: str
    reservation: ReservationOut


class CompleteVisitRequest(BaseModel):
    reservation_uuids: list[str]


class CompletedVisitOut(BaseModel):
    uuid: str
    reservation_uuid: str
    user_uuid: str
    nickname: str
    sale_date: date
    schedule_title: str
    product_name: str
    quantity: int
    time_slot_label: str
    no_show: bool
    completed_at: datetime


class DayCloseRequest(BaseModel):
    sale_date: date | None = Field(None, description="비우면 서버 실행 지역 기준 오늘 날짜")
    confirm_no_show: bool = Field(False, description="마감 시 미방문 예약이 노쇼 처리됨을 관리자가 확인했을 때 true")


class PendingDayCloseOut(BaseModel):
    sale_date: date
    schedule_count: int
    reserved_reservations: int
    visited_reservations: int
    no_show_reservations: int
    cancelled_reservations: int
    expected_no_show_users: int


class DayCloseOut(BaseModel):
    sale_date: date
    closed_at: datetime
    processed_reservations: int
    visited_reservations: int
    no_show_users: int
    no_show_reservations: int
    auto_banned_users: int


class BannedUserOut(BaseModel):
    id: int
    user_uuid: str
    nickname: str
    reason: str
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BannedUserIn(BaseModel):
    user_uuid: str = Field(..., examples=["7f3b7e0a-9f3f-4fa7-94c7-0f49a623e990"])
    nickname: str = Field("", examples=["피카츄트레이너"])
    reason: str = Field("수동 등록", examples=["반복 노쇼"])
    active: bool = True


class SettingUpdate(BaseModel):
    value: str


class SettingOut(BaseModel):
    key: str
    value: str
