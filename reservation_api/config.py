from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _csv_env(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    project_id: str
    service_name: str
    environment: str
    database_url: str
    is_dev_mode: bool
    secret_key: str
    admin_id: str
    admin_password: str
    kakao_rest_api_key: str
    kakao_client_secret: str
    kakao_token_url: str
    kakao_userinfo_url: str
    otp_secret_key: str
    otp_timeout_second: int
    otp_delta: int
    otp_qr_hash_algorithm: str
    otp_manual_hash_algorithm: str
    otp_manual_code_length: int
    admin_login_fail_limit: int
    anti_macro_duration: int
    anti_macro_retention_seconds: int
    cors_allowed_origins: list[str]
    display_timezone_label: str
    app_timezone: str
    message_bus_backend: str
    visit_event_topic: str


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
    return Settings(
        project_id=os.getenv("GCP_PROJECT", os.getenv("GOOGLE_CLOUD_PROJECT", "reservation-io")),
        service_name=os.getenv("SERVICE_NAME", "reservation-api"),
        environment=os.getenv("APP_ENV", "local"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./.local/reservation_api.db"),
        is_dev_mode=_bool_env("IS_DEV_MODE", False),
        secret_key=secret_key,
        admin_id=os.getenv("ADMIN_ID", "admin"),
        admin_password=os.getenv("ADMIN_PASSWORD", "admin"),
        kakao_rest_api_key=os.getenv("KAKAO_REST_API_KEY", ""),
        kakao_client_secret=os.getenv("KAKAO_CLIENT_SECRET", ""),
        kakao_token_url=os.getenv("KAKAO_TOKEN_URL", "https://kauth.kakao.com/oauth/token"),
        kakao_userinfo_url=os.getenv("KAKAO_USERINFO_URL", "https://kapi.kakao.com/v2/user/me"),
        otp_secret_key=os.getenv("OTP_SECRET_KEY", secret_key),
        otp_timeout_second=max(5, _int_env("OTP_TIMEOUT_SECOND", 30)),
        otp_delta=max(0, _int_env("OTP_DELTA", 1)),
        otp_qr_hash_algorithm=os.getenv("OTP_QR_HASH_ALGORITHM", "md5"),
        otp_manual_hash_algorithm=os.getenv("OTP_MANUAL_HASH_ALGORITHM", "sha1"),
        otp_manual_code_length=max(6, _int_env("OTP_MANUAL_CODE_LENGTH", 12)),
        admin_login_fail_limit=max(1, _int_env("ADMIN_LOGIN_FAIL_LIMIT", 5)),
        anti_macro_duration=max(1, _int_env("ANTI_MACRO_DURATION", 10)),
        anti_macro_retention_seconds=max(60, _int_env("ANTI_MACRO_RETENTION_SECONDS", 3600)),
        cors_allowed_origins=_csv_env("CORS_ALLOWED_ORIGINS", "http://localhost:3000"),
        display_timezone_label=os.getenv("DISPLAY_TIMEZONE_LABEL", "Seoul, KR"),
        app_timezone=os.getenv("APP_TIMEZONE", "Asia/Seoul"),
        message_bus_backend=os.getenv("MESSAGE_BUS_BACKEND", "none"),
        visit_event_topic=os.getenv("VISIT_EVENT_TOPIC", ""),
    )
